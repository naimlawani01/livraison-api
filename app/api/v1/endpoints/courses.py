from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, func
from typing import List, Optional
from datetime import datetime, timezone
from ....core.database import get_db
from ....core.config import settings
from ....models.course import Course, CourseStatus, ModePaiement
from ....models.expediteur import Expediteur
from ....models.livreur import Livreur
from ....models.user import User, UserRole
from ....models.wallet_transaction import WalletTransaction
from ....schemas.course import (
    CourseCreate,
    CourseResponse,
    CourseEvaluation,
    CourseAnnulation,
    CourseWithDetails,
    CourseDisponibleResponse
)
from ....services.matching_service import MatchingService
from ....services.geolocation_service import GeolocationService
from ....services.notification_service import notification_service
from ....services import pricing, credit_service, soldes
from ....utils.dependencies import get_current_expediteur, get_current_livreur, get_current_user
import logging
import secrets

logger = logging.getLogger(__name__)

# ── Machine à états : transitions autorisées ──────────────────────────────────
TRANSITIONS_VALIDES = {
    CourseStatus.ACCEPTEE:        [CourseStatus.EN_RECUPERATION],
    CourseStatus.EN_RECUPERATION: [CourseStatus.EN_LIVRAISON],
    CourseStatus.EN_LIVRAISON:    [CourseStatus.TERMINEE],
}


async def _get_user_device_token(db: AsyncSession, user_id) -> Optional[str]:
    """Récupérer le device_token d'un utilisateur"""
    q = select(User).where(User.id == user_id)
    r = await db.execute(q)
    u = r.scalar_one_or_none()
    return u.device_token if u else None

router = APIRouter()

# ── Multiplicateurs nature du colis ───────────────────────────────────────────
_MULT_COLIS: dict[str, float] = {
    "standard":    1.0,
    "alimentaire": 1.1,
    "fragile":     1.3,
    "documents":   0.9,
    "volumineux":  1.5,
}

def _mult_heure(heure: int) -> float:
    """M_heure : créneau normal=1.0, soirée=1.2, nuit=1.5"""
    if 6 <= heure < 20:
        return 1.0
    elif 20 <= heure < 23:
        return 1.2
    return 1.5


def calculer_prix(distance_km: float, nature_colis: str = "standard") -> float:
    """Formule unifiée P = (P_base + d × T_km) × M_colis × M_heure.

    Arrondi aux 500 GNF supérieurs, minimum P_base.
    Utilisée à la création (si position connue) ET au partage GPS du client.
    """
    P_base = 10_000
    T_km = 1_500
    M_colis = _MULT_COLIS.get(str(nature_colis).lower(), 1.0)
    M_heure = _mult_heure(datetime.now().hour)
    prix_brut = (P_base + distance_km * T_km) * M_colis * M_heure
    return max(P_base, round(prix_brut / 500) * 500)


@router.post("/estimer-prix")
async def estimer_prix(
    data: dict,
    expediteur: Expediteur = Depends(get_current_expediteur),
    db: AsyncSession = Depends(get_db)
):
    """
    Estimer le prix selon la formule :
        P = (P_base + d_km × T_km) × M_colis × M_heure
    P_base = 10 000 GNF  |  T_km = 1 500 GNF/km
    """
    if not expediteur.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Votre expediteur doit être vérifié par un administrateur avant de pouvoir créer des courses"
        )

    lat = data.get("latitude_client")
    lng = data.get("longitude_client")
    if not lat or not lng:
        raise HTTPException(400, detail="Coordonnées du client requises")

    nature = str(data.get("nature_colis", "standard")).lower()

    distance_km = GeolocationService.calculer_distance(
        (expediteur.latitude, expediteur.longitude),
        (lat, lng)
    )
    duree = GeolocationService.estimer_duree_trajet(distance_km)

    # Source unique : app/services/pricing.py (12 %, plancher 10 000 + 1 500/km,
    # colis simplifié, sans surge). Aperçu identique au prix réellement créé.
    tarif = pricing.calculer_tarif(distance_km, nature)

    return {
        "distance_km":          round(distance_km, 2),
        "duree_estimee_minutes": duree,
        "prix_estime":           tarif.prix,
        "commission_plateforme": tarif.commission,
        "montant_livreur":       tarif.gain_livreur,
        "multiplicateur_colis":  tarif.mult_colis,
        "multiplicateur_heure":  1.0,
    }


@router.post("/", response_model=CourseResponse, status_code=status.HTTP_201_CREATED)
async def create_course(
    course_data: CourseCreate,
    expediteur: Expediteur = Depends(get_current_expediteur),
    db: AsyncSession = Depends(get_db)
):
    """Créer une nouvelle course de livraison.

    Le prix dépend de la position du client (distance) et de la nature du
    colis. Comme la position n'est généralement pas connue à la création :

    - Si position client fournie  → prix calculé tout de suite, paiement
      GeniusPay créé immédiatement (si MM), course diffusée
    - Si position absente          → prix de base provisoire, pas de
      paiement GeniusPay, pas de diffusion. Le client recevra un lien de
      partage de position. Le prix sera recalculé puis le paiement créé
      dans `/loc/{token}/submit` après partage.
    """
    if not expediteur.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Votre expediteur doit être vérifié par un administrateur avant de pouvoir créer des courses"
        )

    has_position = (
        course_data.latitude_client is not None
        and course_data.longitude_client is not None
    )

    distance_km = None
    duree_estimee = None

    if has_position:
        distance_km = GeolocationService.calculer_distance(
            (expediteur.latitude, expediteur.longitude),
            (course_data.latitude_client, course_data.longitude_client),
        )
        duree_estimee = GeolocationService.estimer_duree_trajet(distance_km)
        # Prix calculé côté backend (autorité) — jamais la valeur du client mobile.
        tarif = pricing.calculer_tarif(distance_km, course_data.nature_colis)
    else:
        # Position inconnue → prix provisoire au plancher. Il sera recalculé au
        # partage GPS du client (location.py) et le Crédit ajusté du delta.
        tarif = pricing.calculer_tarif(0, course_data.nature_colis)

    prix_final = tarif.prix
    commission = tarif.commission
    montant_livreur = tarif.gain_livreur

    # Code livraison crypto-safe (cf. ....core.security.generate_delivery_code)
    from ....core.security import generate_delivery_code
    code_livraison = generate_delivery_code() if course_data.exige_code_livraison else None

    course = Course(
        numero_course=Course.generer_numero_course(),
        expediteur_id=expediteur.id,
        adresse_client=course_data.adresse_client,
        latitude_client=course_data.latitude_client,
        longitude_client=course_data.longitude_client,
        contact_client_nom=course_data.contact_client_nom,
        contact_client_telephone=course_data.contact_client_telephone,
        instructions_speciales=course_data.instructions_speciales,
        description_colis=course_data.description_colis,
        nature_colis=course_data.nature_colis,
        prix_propose=prix_final,
        commission_plateforme=commission,
        montant_livreur=montant_livreur,
        mode_paiement=course_data.mode_paiement,
        distance_km=distance_km,
        duree_estimee_minutes=duree_estimee,
        status=CourseStatus.CREEE,
        tracking_token=secrets.token_hex(10),
        # Toujours générer un location_token — il sert même si la position
        # est connue (le client peut malgré tout consulter / corriger).
        location_token=secrets.token_hex(10),
        exige_code_livraison=course_data.exige_code_livraison,
        code_livraison=code_livraison,
    )

    db.add(course)
    await db.flush()  # obtient course.id sans committer (FK du débit Crédit)

    # Réserver la commission sur le Crédit de l'expéditeur — courses CASH uniquement.
    # (Le MoBILE MONEY est encaissé auprès du client, le Crédit n'est pas concerné.)
    # Le débit atomique EST le garde-fou : Crédit insuffisant → rien n'est créé.
    if course.mode_paiement == ModePaiement.CASH:
        try:
            await credit_service.debiter_commission(
                db, expediteur.id, commission,
                course_id=course.id,
                description=f"Commission course #{course.numero_course}",
            )
        except soldes.SoldeInsuffisant:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Crédit insuffisant pour créer cette course. Rechargez votre Crédit.",
            )
    else:
        await db.commit()
    await db.refresh(course)

    checkout_url: Optional[str] = None

    if has_position:
        # Position connue → on peut traiter immédiatement
        if (
            course_data.mode_paiement == ModePaiement.MOBILE_MONEY
            and settings.GENIUSPAY_API_KEY
        ):
            try:
                from ....services import genius_pay_service
                paiement = await genius_pay_service.initier_paiement(
                    course_id=str(course.id),
                    expediteur_id=str(expediteur.id),
                    montant=course.prix_propose,
                    description=f"Livraison {course.numero_course}",
                    nom_client=course.contact_client_nom,
                )
                course.geniuspay_reference = paiement.get("reference")
                course.geniuspay_checkout_url = paiement.get("checkout_url")
                checkout_url = course.geniuspay_checkout_url
                await db.commit()
                # MM : on attend le webhook payment.success pour diffuser
            except Exception as e:  # noqa: BLE001
                logger.error(f"GeniusPay initier_paiement échoué: {e} — diffusion directe en fallback")
                await MatchingService.diffuser_course(
                    db, course, expediteur.latitude, expediteur.longitude,
                    expediteur_nom=expediteur.nom,
                )
        else:
            # Cash → diffuser immédiatement
            await MatchingService.diffuser_course(
                db, course, expediteur.latitude, expediteur.longitude,
                expediteur_nom=expediteur.nom,
            )
    # else : position absente → on ne diffuse PAS, on attend que le client
    # partage sa position via /loc/{token} qui s'occupera du calcul prix +
    # paiement (si MM) + diffusion.

    # ── SMS unifié au client ──
    # Le lien envoyé dépend de l'état :
    #   • Position connue + Cash       → /suivi/{tracking_token}
    #   • Position connue + MM payable → /suivi/{tracking_token} (avec checkout dans le SMS)
    #   • Position absente             → /loc/{location_token} (page partage)
    try:
        from ....services.sms_service import sms_service
        if has_position:
            action_url = f"{settings.PUBLIC_BASE_URL}/suivi/{course.tracking_token}"
        else:
            action_url = f"{settings.PUBLIC_BASE_URL}/loc/{course.location_token}"
        await sms_service.envoyer_sms_course(
            telephone=course.contact_client_telephone,
            nom_client=course.contact_client_nom,
            numero_course=course.numero_course,
            expediteur_nom=expediteur.nom,
            montant=course.prix_propose if has_position else 0,
            tracking_url=action_url,
            checkout_url=checkout_url,
            position_required=not has_position,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"SMS course {course.numero_course} non envoyé : {e}")

    await db.refresh(course)
    return course


@router.get("/me")
async def get_my_courses(
    expediteur: Expediteur = Depends(get_current_expediteur),
    status_filter: str = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    """Obtenir mes courses (expediteur) — paginé"""
    base = select(Course).where(Course.expediteur_id == expediteur.id)
    
    if status_filter:
        base = base.where(Course.status == status_filter)
    
    # Total
    count_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_q)).scalar() or 0
    
    # Page
    query = base.order_by(Course.created_at.desc()).offset((page - 1) * limit).limit(limit)
    result = await db.execute(query)
    courses = result.scalars().all()
    
    return {
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit,
        "courses": [CourseResponse.model_validate(c).model_dump() for c in courses],
    }


@router.get("/livreur/disponibles", response_model=List[CourseDisponibleResponse])
async def get_courses_disponibles(
    lat: Optional[float] = Query(None, description="Latitude du livreur"),
    lon: Optional[float] = Query(None, description="Longitude du livreur"),
    rayon: float = Query(10.0, description="Rayon de recherche en km"),
    livreur: Livreur = Depends(get_current_livreur),
    db: AsyncSession = Depends(get_db)
):
    """Obtenir les courses disponibles à proximité du livreur"""
    if not livreur.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Votre compte doit être vérifié par un administrateur pour voir les courses disponibles"
        )

    if not livreur.is_disponible:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Vous devez être en ligne pour voir les courses disponibles"
        )

    livreur_lat = lat if lat is not None else livreur.latitude
    livreur_lon = lon if lon is not None else livreur.longitude
    has_position = livreur_lat is not None and livreur_lon is not None

    # JOIN unique au lieu de N+1 requêtes
    query = (
        select(Course, Expediteur)
        .join(Expediteur, Course.expediteur_id == Expediteur.id)
        .where(Course.status == CourseStatus.DIFFUSEE)
        .order_by(Course.created_at.desc())
    )
    result = await db.execute(query)
    rows = result.all()

    courses_proches = []
    for course, expediteur in rows:
        distance_livreur = None
        duree_livreur = None

        if has_position and expediteur.latitude and expediteur.longitude:
            distance_livreur = GeolocationService.calculer_distance(
                (livreur_lat, livreur_lon),
                (expediteur.latitude, expediteur.longitude)
            )
            if distance_livreur > rayon:
                continue
            duree_livreur = GeolocationService.estimer_duree_trajet(distance_livreur)

        courses_proches.append(CourseDisponibleResponse(
            id=course.id,
            numero_course=course.numero_course,
            expediteur_id=course.expediteur_id,
            adresse_client=course.adresse_client,
            latitude_client=course.latitude_client,
            longitude_client=course.longitude_client,
            contact_client_nom=course.contact_client_nom,
            contact_client_telephone=course.contact_client_telephone,
            instructions_speciales=course.instructions_speciales,
            description_colis=course.description_colis,
            prix_propose=course.prix_propose,
            commission_plateforme=course.commission_plateforme,
            montant_livreur=course.montant_livreur,
            distance_km=course.distance_km,
            duree_estimee_minutes=course.duree_estimee_minutes,
            status=course.status,
            created_at=course.created_at,
            mode_paiement=course.mode_paiement,
            paiement_confirme=course.paiement_confirme,
            exige_code_livraison=course.exige_code_livraison,
            expediteur_nom=expediteur.nom,
            expediteur_adresse=expediteur.adresse,
            expediteur_latitude=expediteur.latitude,
            expediteur_longitude=expediteur.longitude,
            distance_livreur_km=round(distance_livreur, 2) if distance_livreur is not None else None,
            duree_livreur_minutes=duree_livreur,
        ))

    courses_proches.sort(key=lambda c: c.distance_livreur_km or 999)

    return courses_proches


@router.get("/livreur/mes-courses")
async def get_mes_courses(
    livreur: Livreur = Depends(get_current_livreur),
    status_filter: str = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    """Obtenir mes courses (livreur) — paginé"""
    base = select(Course).where(Course.livreur_id == livreur.id)
    
    if status_filter:
        base = base.where(Course.status == status_filter)
    
    # Total
    count_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_q)).scalar() or 0
    
    # Page
    query = base.order_by(Course.created_at.desc()).offset((page - 1) * limit).limit(limit)
    result = await db.execute(query)
    courses = result.scalars().all()
    
    return {
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit,
        "courses": [CourseResponse.model_validate(c).model_dump() for c in courses],
    }


@router.post("/{course_id}/accepter", response_model=CourseResponse)
async def accepter_course(
    course_id: str,
    livreur: Livreur = Depends(get_current_livreur),
    db: AsyncSession = Depends(get_db)
):
    """Accepter une course (livreur)"""
    if not livreur.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Votre compte doit être vérifié par un administrateur pour accepter des courses"
        )

    query = select(Course).where(Course.id == course_id)
    result = await db.execute(query)
    course = result.scalar_one_or_none()
    
    if not course:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Course non trouvée"
        )
    
    # Vérifications avec messages clairs
    if course.status not in (CourseStatus.CREEE, CourseStatus.DIFFUSEE):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cette course a déjà été prise par un autre livreur"
        )
    
    if not livreur.is_disponible:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vous devez être en ligne pour accepter une course"
        )

    # Nouveau modèle : plus de garde-fou côté livreur. La commission est réservée
    # sur le Crédit de l'expéditeur à la création de la course ; le livreur, lui,
    # est réglé en cash par l'expéditeur — il n'a aucune avance à faire.

    # Compter les courses actives du livreur
    active_statuses = [CourseStatus.ACCEPTEE, CourseStatus.EN_RECUPERATION, CourseStatus.EN_LIVRAISON]
    count_query = select(func.count()).where(
        Course.livreur_id == livreur.id,
        Course.status.in_(active_statuses)
    )
    count_result = await db.execute(count_query)
    nb_courses_actives = count_result.scalar() or 0
    
    max_courses = settings.MAX_COURSES_SIMULTANEES
    if nb_courses_actives >= max_courses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Vous avez déjà {nb_courses_actives} course{'s' if nb_courses_actives > 1 else ''} en cours (max {max_courses}). Terminez-en une pour en accepter une nouvelle."
        )
    
    # Accepter la course
    success = await MatchingService.accepter_course(db, course, livreur)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Impossible d'accepter cette course"
        )
    
    await db.refresh(course)
    
    # Notifier le expediteur que sa course a été acceptée
    try:
        expediteur_query = select(Expediteur).where(Expediteur.id == course.expediteur_id)
        expediteur_result = await db.execute(expediteur_query)
        expediteur_notif = expediteur_result.scalar_one_or_none()
        if expediteur_notif:
            token = await _get_user_device_token(db, expediteur_notif.user_id)
            if token:
                await notification_service.notifier_course_acceptee(
                    device_token=token,
                    livreur_nom=livreur.nom_complet,
                    numero_course=course.numero_course,
                )
    except Exception as e:
        logger.warning(f"Notification acceptation échouée: {e}")
    
    return course


@router.patch("/{course_id}/statut", response_model=CourseResponse)
async def update_course_status(
    course_id: str,
    nouveau_statut: CourseStatus,
    code_livraison: Optional[str] = None,
    livreur: Livreur = Depends(get_current_livreur),
    db: AsyncSession = Depends(get_db)
):
    """Mettre à jour le statut d'une course (livreur) — transitions validées"""
    query = select(Course).where(
        Course.id == course_id,
        Course.livreur_id == livreur.id
    )
    result = await db.execute(query)
    course = result.scalar_one_or_none()
    
    if not course:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Course non trouvée"
        )
    
    # Valider la transition d'état
    allowed = TRANSITIONS_VALIDES.get(course.status, [])
    if nouveau_statut not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Transition invalide : {course.status.value} → {nouveau_statut.value}. "
                   f"Transitions possibles : {[s.value for s in allowed]}"
        )
    
    course.status = nouveau_statut
    
    if nouveau_statut == CourseStatus.EN_RECUPERATION:
        course.recuperee_at = datetime.now(timezone.utc)
    elif nouveau_statut == CourseStatus.EN_LIVRAISON:
        pass  # Déjà en route
    elif nouveau_statut == CourseStatus.TERMINEE:
        if course.exige_code_livraison:
            # Anti brute-force : le code n'a que 4 chiffres. On limite à 5 essais
            # par 15 min et par course, sinon un livreur malhonnête pourrait le
            # deviner et confirmer une livraison non remise.
            from ....core.redis import redis_client
            _attempts_key = f"code_attempts:{course.id}"
            _attempts = int(await redis_client.get(_attempts_key) or 0)
            if _attempts >= 5:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Trop de tentatives de code. Réessayez dans 15 minutes.",
                )
            if not code_livraison or code_livraison != course.code_livraison:
                await redis_client.incr(_attempts_key)
                await redis_client.expire(_attempts_key, 900)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Code de livraison invalide ou manquant"
                )
            await redis_client.delete(_attempts_key)  # succès → reset
        course.livree_at = datetime.now(timezone.utc)
        livreur.nombre_courses_completees += 1
        livreur.total_gains += course.montant_livreur  # gains totaux (cash + plateforme) — statistique

        if course.mode_paiement == ModePaiement.MOBILE_MONEY:
            # La plateforme a encaissé le client → crédite les Gains (retirables) du livreur.
            solde_avant = livreur.solde_disponible
            livreur.solde_disponible = soldes.gains_crediter(solde_avant, course.montant_livreur)
            txn = WalletTransaction(
                livreur_id=livreur.id,
                type="credit",
                montant=course.montant_livreur,
                solde_avant=solde_avant,
                solde_apres=livreur.solde_disponible,
                description=f"Course #{course.numero_course} (Mobile Money)",
                course_id=course.id,
                statut="complete",
            )
            db.add(txn)
        # CASH : le livreur a été réglé en espèces directement par l'expéditeur, et la
        # commission a déjà été prélevée sur le Crédit de l'expéditeur à la création de
        # la course. Rien à débiter côté livreur — plus de dette, plus de solde négatif.
        
        # Vérifier s'il reste d'autres courses actives
        other_active_query = select(func.count()).where(
            Course.livreur_id == livreur.id,
            Course.id != course.id,
            Course.status.in_([CourseStatus.ACCEPTEE, CourseStatus.EN_RECUPERATION, CourseStatus.EN_LIVRAISON])
        )
        other_result = await db.execute(other_active_query)
        other_count = other_result.scalar() or 0
        
        if other_count == 0:
            livreur.is_en_course = False
            # is_disponible reste inchangé : le livreur décide lui-même de se remettre en ligne
    
    await db.commit()
    await db.refresh(course)
    
    # Notifier le expediteur du changement de statut
    try:
        expediteur_query = select(Expediteur).where(Expediteur.id == course.expediteur_id)
        expediteur_result = await db.execute(expediteur_query)
        expediteur_notif = expediteur_result.scalar_one_or_none()
        if expediteur_notif:
            token = await _get_user_device_token(db, expediteur_notif.user_id)
            if token:
                await notification_service.notifier_changement_status(
                    device_token=token,
                    status=nouveau_statut.value,
                    numero_course=course.numero_course,
                )
    except Exception as e:
        logger.warning(f"Notification changement statut échouée: {e}")
    
    return course


@router.post("/{course_id}/annuler", response_model=CourseResponse)
async def annuler_course(
    course_id: str,
    annulation: CourseAnnulation,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Annuler une course (expediteur propriétaire, livreur assigné, ou admin)"""
    query = select(Course).where(Course.id == course_id)
    result = await db.execute(query)
    course = result.scalar_one_or_none()
    
    if not course:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Course non trouvée"
        )
    
    # Vérifier les droits d'accès
    is_admin = current_user.role == UserRole.ADMIN
    is_expediteur_owner = False
    is_livreur_assigned = False
    if current_user.role == UserRole.EXPEDITEUR:
        p_q = select(Expediteur).where(Expediteur.user_id == current_user.id)
        p_r = await db.execute(p_q)
        p = p_r.scalar_one_or_none()
        is_expediteur_owner = p and course.expediteur_id == p.id
    elif current_user.role == UserRole.LIVREUR:
        l_q = select(Livreur).where(Livreur.user_id == current_user.id)
        l_r = await db.execute(l_q)
        l = l_r.scalar_one_or_none()
        is_livreur_assigned = l and course.livreur_id == l.id

    if not (is_admin or is_expediteur_owner or is_livreur_assigned):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Vous n'êtes pas autorisé à annuler cette course"
        )
    
    # Vérifier que la course peut être annulée
    non_annulable = [CourseStatus.TERMINEE, CourseStatus.ANNULEE]
    if course.status in non_annulable:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cette course ne peut plus être annulée"
        )
    
    # Si un livreur était assigné, le libérer
    if course.livreur_id:
        livreur_query = select(Livreur).where(Livreur.id == course.livreur_id)
        livreur_result = await db.execute(livreur_query)
        livreur = livreur_result.scalar_one_or_none()
        if livreur:
            # Vérifier s'il a d'autres courses actives
            other_active = select(func.count()).where(
                Course.livreur_id == livreur.id,
                Course.id != course.id,
                Course.status.in_([CourseStatus.ACCEPTEE, CourseStatus.EN_RECUPERATION, CourseStatus.EN_LIVRAISON])
            )
            other_result = await db.execute(other_active)
            if (other_result.scalar() or 0) == 0:
                livreur.is_en_course = False
                livreur.is_disponible = True
    
    # Rembourser la commission réservée sur le Crédit de l'expéditeur (courses CASH).
    if course.mode_paiement == ModePaiement.CASH and course.commission_plateforme:
        try:
            await credit_service.rembourser_commission(
                db, course.expediteur_id, course.commission_plateforme,
                course_id=course.id,
                description=f"Remboursement course annulée #{course.numero_course}",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Remboursement Crédit échoué (course %s): %s", course.id, e)

    course.status = CourseStatus.ANNULEE
    course.annulee_at = datetime.now(timezone.utc)
    course.raison_annulation = annulation.raison

    await db.commit()
    await db.refresh(course)
    
    # Notifier le livreur (si assigné) et le expediteur de l'annulation
    try:
        raison = annulation.raison or ""
        data = {"type": "course_annulee", "numero_course": course.numero_course, "raison": raison}

        # Notifier le livreur assigné
        if course.livreur_id:
            livreur_q = select(Livreur).where(Livreur.id == course.livreur_id)
            livreur_r = await db.execute(livreur_q)
            liv = livreur_r.scalar_one_or_none()
            if liv:
                token = await _get_user_device_token(db, liv.user_id)
                if token:
                    if is_expediteur_owner:
                        msg_livreur = f"Le commerce a annulé la course #{course.numero_course}."
                    elif is_admin:
                        msg_livreur = f"La course #{course.numero_course} a été annulée par l'admin."
                    else:
                        msg_livreur = f"Course #{course.numero_course} annulée."
                    if raison:
                        msg_livreur += f" Motif : {raison}"
                    await notification_service.envoyer_notification_push(
                        token, titre="Course annulée", message=msg_livreur, data=data
                    )

        # Notifier le expediteur
        expediteur_q = select(Expediteur).where(Expediteur.id == course.expediteur_id)
        expediteur_r = await db.execute(expediteur_q)
        expediteur_notif = expediteur_r.scalar_one_or_none()
        if expediteur_notif:
            token = await _get_user_device_token(db, expediteur_notif.user_id)
            if token:
                if is_livreur_assigned:
                    msg_expediteur = f"Le livreur a annulé la course #{course.numero_course}."
                elif is_admin:
                    msg_expediteur = f"La course #{course.numero_course} a été annulée par l'admin."
                else:
                    msg_expediteur = f"Course #{course.numero_course} annulée."
                if raison:
                    msg_expediteur += f" Motif : {raison}"
                await notification_service.envoyer_notification_push(
                    token, titre="Course annulée", message=msg_expediteur, data=data
                )
    except Exception as e:
        logger.warning(f"Notification annulation échouée: {e}")
    
    return course


@router.post("/{course_id}/evaluer", response_model=CourseResponse)
async def evaluer_livreur(
    course_id: str,
    evaluation: CourseEvaluation,
    expediteur: Expediteur = Depends(get_current_expediteur),
    db: AsyncSession = Depends(get_db)
):
    """Évaluer le livreur après livraison (expediteur)"""
    query = select(Course).where(
        Course.id == course_id,
        Course.expediteur_id == expediteur.id,
        Course.status == CourseStatus.TERMINEE
    )
    result = await db.execute(query)
    course = result.scalar_one_or_none()
    
    if not course:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Course non trouvée ou non terminée"
        )
    
    # Enregistrer l'évaluation
    course.note_livreur = evaluation.note_livreur
    course.commentaire_livreur = evaluation.commentaire_livreur
    
    # Mettre à jour la note du livreur
    if course.livreur_id:
        livreur_query = select(Livreur).where(Livreur.id == course.livreur_id)
        livreur_result = await db.execute(livreur_query)
        livreur = livreur_result.scalar_one_or_none()
        
        if livreur:
            total_notes = livreur.note_moyenne * livreur.nombre_evaluations
            livreur.nombre_evaluations += 1
            livreur.note_moyenne = (total_notes + evaluation.note_livreur) / livreur.nombre_evaluations
    
    await db.commit()
    await db.refresh(course)
    
    return course


@router.post("/{course_id}/confirmer-paiement", response_model=CourseResponse)
async def confirmer_paiement(
    course_id: str,
    livreur: Livreur = Depends(get_current_livreur),
    db: AsyncSession = Depends(get_db)
):
    """Confirmer le paiement d'une course (livreur assigné uniquement)"""
    query = select(Course).where(Course.id == course_id)
    result = await db.execute(query)
    course = result.scalar_one_or_none()
    
    if not course:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Course non trouvée"
        )
    
    if course.livreur_id != livreur.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Seul le livreur assigné peut confirmer le paiement"
        )

    # Le Mobile Money est confirmé automatiquement par le webhook GeniusPay :
    # cet endpoint ne sert qu'à la confirmation manuelle du CASH.
    if course.mode_paiement == ModePaiement.MOBILE_MONEY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le paiement Mobile Money est confirmé automatiquement."
        )

    if course.paiement_confirme == "oui":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le paiement a déjà été confirmé"
        )
    
    course.paiement_confirme = "oui"
    await db.commit()
    await db.refresh(course)
    
    return course


@router.get("/{course_id}", response_model=CourseWithDetails)
async def get_course_details(
    course_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Obtenir les détails d'une course (expediteur propriétaire, livreur assigné, ou admin)"""
    query = select(Course).where(Course.id == course_id)
    result = await db.execute(query)
    course = result.scalar_one_or_none()
    
    if not course:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Course non trouvée"
        )
    
    # Vérifier les droits d'accès
    is_admin = current_user.role == UserRole.ADMIN
    is_expediteur_owner = False
    is_livreur_assigned = False
    if current_user.role == UserRole.EXPEDITEUR:
        p_q = select(Expediteur).where(Expediteur.user_id == current_user.id)
        p_r = await db.execute(p_q)
        p = p_r.scalar_one_or_none()
        is_expediteur_owner = p and course.expediteur_id == p.id
    elif current_user.role == UserRole.LIVREUR:
        l_q = select(Livreur).where(Livreur.user_id == current_user.id)
        l_r = await db.execute(l_q)
        l = l_r.scalar_one_or_none()
        is_livreur_assigned = l and course.livreur_id == l.id

    if not (is_admin or is_expediteur_owner or is_livreur_assigned):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Vous n'êtes pas autorisé à consulter cette course"
        )
    
    # Récupérer les détails expediteur et livreur
    response_dict = CourseResponse.model_validate(course).model_dump()
    
    # Ajouter le expediteur
    if course.expediteur_id:
        expediteur_query = select(Expediteur).where(Expediteur.id == course.expediteur_id)
        expediteur_result = await db.execute(expediteur_query)
        expediteur = expediteur_result.scalar_one_or_none()
        if expediteur:
            response_dict["expediteur"] = {
                "nom": expediteur.nom,
                "adresse": expediteur.adresse
            }
    
    # Ajouter le livreur
    if course.livreur_id:
        livreur_query = select(Livreur).where(Livreur.id == course.livreur_id)
        livreur_result = await db.execute(livreur_query)
        livreur = livreur_result.scalar_one_or_none()
        if livreur:
            user_query = select(User).where(User.id == livreur.user_id)
            user_result = await db.execute(user_query)
            user = user_result.scalar_one_or_none()
            
            response_dict["livreur"] = {
                "nom_complet": livreur.nom_complet,
                "note_moyenne": livreur.note_moyenne,
                "telephone": user.phone if user else None,
                "type_vehicule": livreur.type_vehicule,
                "nombre_courses_completees": livreur.nombre_courses_completees,
                "latitude": livreur.latitude,
                "longitude": livreur.longitude,
            }
    
    # Masquer le code_livraison pour le livreur (seul le client le connaît)
    if is_livreur_assigned and not is_admin:
        response_dict["code_livraison"] = None
    
    return response_dict
