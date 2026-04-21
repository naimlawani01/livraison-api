from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, func
from typing import List, Optional
from datetime import datetime
from ....core.database import get_db
from ....core.config import settings
from ....models.commande import Commande, CommandeStatus
from ....models.partenaire import Partenaire
from ....models.livreur import Livreur
from ....models.user import User
from ....schemas.commande import (
    CommandeCreate,
    CommandeResponse,
    CommandeEvaluation,
    CommandeAnnulation,
    CommandeWithDetails,
    CommandeDisponibleResponse
)
from ....services.matching_service import MatchingService
from ....services.geolocation_service import GeolocationService
from ....services.notification_service import notification_service
from ....utils.dependencies import get_current_partenaire, get_current_livreur, get_current_user
import logging
import secrets

logger = logging.getLogger(__name__)


async def _get_user_device_token(db: AsyncSession, user_id) -> Optional[str]:
    """Récupérer le device_token d'un utilisateur"""
    q = select(User).where(User.id == user_id)
    r = await db.execute(q)
    u = r.scalar_one_or_none()
    return u.device_token if u else None

router = APIRouter()


@router.post("/estimer-prix")
async def estimer_prix(
    data: dict,  # {adresse_client: str, latitude_client: float, longitude_client: float}
    partenaire: Partenaire = Depends(get_current_partenaire),
    db: AsyncSession = Depends(get_db)
):
    """Estimer le prix de livraison en fonction de la distance"""
    if not partenaire.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Votre partenaire doit être vérifié par un administrateur avant de pouvoir créer des commandes"
        )

    lat = data.get("latitude_client")
    lng = data.get("longitude_client")

    if not lat or not lng:
        raise HTTPException(400, detail="Coordonnées du client requises")

    # Calculer la distance
    distance_km = GeolocationService.calculer_distance(
        (partenaire.latitude, partenaire.longitude),
        (lat, lng)
    )
    duree = GeolocationService.estimer_duree_trajet(distance_km)

    # Prix de base + prix/km
    prix_base = 500  # 500 FCFA de base
    prix_par_km = 200  # 200 FCFA/km
    prix_estime = prix_base + (distance_km * prix_par_km)
    prix_estime = max(500, round(prix_estime / 100) * 100)  # Arrondi aux 100 FCFA, min 500

    commission = prix_estime * (settings.PLATFORM_COMMISSION_PERCENTAGE / 100)
    montant_livreur = prix_estime - commission

    return {
        "distance_km": round(distance_km, 2),
        "duree_estimee_minutes": duree,
        "prix_estime": prix_estime,
        "commission_plateforme": round(commission, 2),
        "montant_livreur": round(montant_livreur, 2),
    }


@router.post("/", response_model=CommandeResponse, status_code=status.HTTP_201_CREATED)
async def create_commande(
    commande_data: CommandeCreate,
    partenaire: Partenaire = Depends(get_current_partenaire),
    db: AsyncSession = Depends(get_db)
):
    """Créer une nouvelle commande de livraison"""
    if not partenaire.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Votre partenaire doit être vérifié par un administrateur avant de pouvoir créer des commandes"
        )

    # Calculer la commission
    commission, montant_livreur = MatchingService.calculer_commission(
        commande_data.prix_propose
    )
    
    # Calculer la distance si coordonnées client fournies
    distance_km = None
    duree_estimee = None
    
    if commande_data.latitude_client and commande_data.longitude_client:
        distance_km = GeolocationService.calculer_distance(
            (partenaire.latitude, partenaire.longitude),
            (commande_data.latitude_client, commande_data.longitude_client)
        )
        duree_estimee = GeolocationService.estimer_duree_trajet(distance_km)
    
    import random
    code_livraison = str(random.randint(1000, 9999)) if commande_data.exige_code_livraison else None
    
    # Créer la commande
    commande = Commande(
        numero_commande=Commande.generer_numero_commande(),
        partenaire_id=partenaire.id,
        adresse_client=commande_data.adresse_client,
        latitude_client=commande_data.latitude_client,
        longitude_client=commande_data.longitude_client,
        contact_client_nom=commande_data.contact_client_nom,
        contact_client_telephone=commande_data.contact_client_telephone,
        instructions_speciales=commande_data.instructions_speciales,
        description_colis=commande_data.description_colis,
        prix_propose=commande_data.prix_propose,
        commission_plateforme=commission,
        montant_livreur=montant_livreur,
        mode_paiement=commande_data.mode_paiement,
        distance_km=distance_km,
        duree_estimee_minutes=duree_estimee,
        status=CommandeStatus.CREEE,
        tracking_token=secrets.token_urlsafe(32),
        exige_code_livraison=commande_data.exige_code_livraison,
        code_livraison=code_livraison,
    )
    
    db.add(commande)
    await db.commit()
    await db.refresh(commande)
    
    # Diffuser la commande aux livreurs proches (passe en DIFFUSEE)
    await MatchingService.diffuser_commande(
        db,
        commande,
        partenaire.latitude,
        partenaire.longitude
    )
    
    # Refresh pour retourner le statut DIFFUSEE au client
    await db.refresh(commande)
    
    return commande


@router.get("/me", response_model=List[CommandeResponse])
async def get_my_commandes(
    partenaire: Partenaire = Depends(get_current_partenaire),
    status_filter: str = None,
    db: AsyncSession = Depends(get_db)
):
    """Obtenir mes commandes (partenaire)"""
    query = select(Commande).where(Commande.partenaire_id == partenaire.id)
    
    if status_filter:
        query = query.where(Commande.status == status_filter)
    
    query = query.order_by(Commande.created_at.desc())
    result = await db.execute(query)
    commandes = result.scalars().all()
    
    return commandes


@router.get("/livreur/disponibles", response_model=List[CommandeDisponibleResponse])
async def get_commandes_disponibles(
    lat: Optional[float] = Query(None, description="Latitude du livreur"),
    lon: Optional[float] = Query(None, description="Longitude du livreur"),
    rayon: float = Query(10.0, description="Rayon de recherche en km"),
    livreur: Livreur = Depends(get_current_livreur),
    db: AsyncSession = Depends(get_db)
):
    """Obtenir les commandes disponibles à proximité du livreur"""
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

    query = select(Commande).where(
        or_(
            Commande.status == CommandeStatus.DIFFUSEE,
            Commande.status == CommandeStatus.CREEE
        )
    ).order_by(Commande.created_at.desc())
    result = await db.execute(query)
    commandes = result.scalars().all()

    commandes_proches = []
    for commande in commandes:
        partenaire_query = select(Partenaire).where(Partenaire.id == commande.partenaire_id)
        partenaire_result = await db.execute(partenaire_query)
        partenaire = partenaire_result.scalar_one_or_none()

        if not partenaire:
            continue

        distance_livreur = None
        duree_livreur = None

        if has_position and partenaire.latitude and partenaire.longitude:
            distance_livreur = GeolocationService.calculer_distance(
                (livreur_lat, livreur_lon),
                (partenaire.latitude, partenaire.longitude)
            )
            if distance_livreur > rayon:
                continue
            duree_livreur = GeolocationService.estimer_duree_trajet(distance_livreur)

        commandes_proches.append(CommandeDisponibleResponse(
            id=commande.id,
            numero_commande=commande.numero_commande,
            partenaire_id=commande.partenaire_id,
            adresse_client=commande.adresse_client,
            latitude_client=commande.latitude_client,
            longitude_client=commande.longitude_client,
            contact_client_nom=commande.contact_client_nom,
            contact_client_telephone=commande.contact_client_telephone,
            instructions_speciales=commande.instructions_speciales,
            description_colis=commande.description_colis,
            prix_propose=commande.prix_propose,
            commission_plateforme=commande.commission_plateforme,
            montant_livreur=commande.montant_livreur,
            distance_km=commande.distance_km,
            duree_estimee_minutes=commande.duree_estimee_minutes,
            status=commande.status,
            created_at=commande.created_at,
            mode_paiement=commande.mode_paiement,
            paiement_confirme=commande.paiement_confirme,
            exige_code_livraison=commande.exige_code_livraison,
            partenaire_nom=partenaire.nom,
            partenaire_adresse=partenaire.adresse,
            partenaire_latitude=partenaire.latitude,
            partenaire_longitude=partenaire.longitude,
            distance_livreur_km=round(distance_livreur, 2) if distance_livreur is not None else None,
            duree_livreur_minutes=duree_livreur,
        ))

    commandes_proches.sort(key=lambda c: c.distance_livreur_km or 999)

    return commandes_proches


@router.get("/livreur/mes-courses", response_model=List[CommandeResponse])
async def get_mes_courses(
    livreur: Livreur = Depends(get_current_livreur),
    status_filter: str = None,
    db: AsyncSession = Depends(get_db)
):
    """Obtenir mes courses (livreur)"""
    query = select(Commande).where(Commande.livreur_id == livreur.id)
    
    if status_filter:
        query = query.where(Commande.status == status_filter)
    
    query = query.order_by(Commande.created_at.desc())
    result = await db.execute(query)
    commandes = result.scalars().all()
    
    return commandes


@router.post("/{commande_id}/accepter", response_model=CommandeResponse)
async def accepter_commande(
    commande_id: str,
    livreur: Livreur = Depends(get_current_livreur),
    db: AsyncSession = Depends(get_db)
):
    """Accepter une commande (livreur)"""
    if not livreur.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Votre compte doit être vérifié par un administrateur pour accepter des courses"
        )

    query = select(Commande).where(Commande.id == commande_id)
    result = await db.execute(query)
    commande = result.scalar_one_or_none()
    
    if not commande:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Commande non trouvée"
        )
    
    # Vérifications avec messages clairs
    if commande.status not in (CommandeStatus.CREEE, CommandeStatus.DIFFUSEE):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cette commande a déjà été prise par un autre livreur"
        )
    
    if not livreur.is_disponible:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vous devez être en ligne pour accepter une course"
        )
    
    # Compter les courses actives du livreur
    active_statuses = [CommandeStatus.ACCEPTEE, CommandeStatus.EN_RECUPERATION, CommandeStatus.EN_LIVRAISON]
    count_query = select(func.count()).where(
        Commande.livreur_id == livreur.id,
        Commande.status.in_(active_statuses)
    )
    count_result = await db.execute(count_query)
    nb_courses_actives = count_result.scalar() or 0
    
    max_courses = settings.MAX_COURSES_SIMULTANEES
    if nb_courses_actives >= max_courses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Vous avez déjà {nb_courses_actives} course{'s' if nb_courses_actives > 1 else ''} en cours (max {max_courses}). Terminez-en une pour en accepter une nouvelle."
        )
    
    # Accepter la commande
    success = await MatchingService.accepter_commande(db, commande, livreur)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Impossible d'accepter cette commande"
        )
    
    await db.refresh(commande)
    
    # Notifier le partenaire que sa commande a été acceptée
    try:
        partenaire_query = select(Partenaire).where(Partenaire.id == commande.partenaire_id)
        partenaire_result = await db.execute(partenaire_query)
        partenaire_notif = partenaire_result.scalar_one_or_none()
        if partenaire_notif:
            token = await _get_user_device_token(db, partenaire_notif.user_id)
            if token:
                await notification_service.notifier_commande_acceptee(
                    device_token=token,
                    livreur_nom=livreur.nom_complet,
                    numero_commande=commande.numero_commande,
                )
    except Exception as e:
        logger.warning(f"Notification acceptation échouée: {e}")
    
    return commande


@router.patch("/{commande_id}/statut", response_model=CommandeResponse)
async def update_commande_status(
    commande_id: str,
    nouveau_statut: CommandeStatus,
    code_livraison: Optional[str] = None,
    livreur: Livreur = Depends(get_current_livreur),
    db: AsyncSession = Depends(get_db)
):
    """Mettre à jour le statut d'une commande (livreur)"""
    query = select(Commande).where(
        Commande.id == commande_id,
        Commande.livreur_id == livreur.id
    )
    result = await db.execute(query)
    commande = result.scalar_one_or_none()
    
    if not commande:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Commande non trouvée"
        )
    
    # Mettre à jour selon le statut
    commande.status = nouveau_statut
    
    if nouveau_statut == CommandeStatus.EN_RECUPERATION:
        commande.recuperee_at = datetime.utcnow()
    elif nouveau_statut == CommandeStatus.EN_LIVRAISON:
        pass  # Déjà en route
    elif nouveau_statut == CommandeStatus.TERMINEE:
        if commande.exige_code_livraison:
            if not code_livraison or code_livraison != commande.code_livraison:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Code de livraison invalide ou manquant"
                )
        commande.livree_at = datetime.utcnow()
        livreur.nombre_courses_completees += 1
        livreur.solde_disponible += commande.montant_livreur
        livreur.total_gains += commande.montant_livreur
        
        # Vérifier s'il reste d'autres courses actives
        other_active_query = select(func.count()).where(
            Commande.livreur_id == livreur.id,
            Commande.id != commande.id,
            Commande.status.in_([CommandeStatus.ACCEPTEE, CommandeStatus.EN_RECUPERATION, CommandeStatus.EN_LIVRAISON])
        )
        other_result = await db.execute(other_active_query)
        other_count = other_result.scalar() or 0
        
        if other_count == 0:
            livreur.is_en_course = False
            livreur.is_disponible = True
    
    await db.commit()
    await db.refresh(commande)
    
    # Notifier le partenaire du changement de statut
    try:
        partenaire_query = select(Partenaire).where(Partenaire.id == commande.partenaire_id)
        partenaire_result = await db.execute(partenaire_query)
        partenaire_notif = partenaire_result.scalar_one_or_none()
        if partenaire_notif:
            token = await _get_user_device_token(db, partenaire_notif.user_id)
            if token:
                await notification_service.notifier_changement_status(
                    device_token=token,
                    status=nouveau_statut.value,
                    numero_commande=commande.numero_commande,
                )
    except Exception as e:
        logger.warning(f"Notification changement statut échouée: {e}")
    
    return commande


@router.post("/{commande_id}/annuler", response_model=CommandeResponse)
async def annuler_commande(
    commande_id: str,
    annulation: CommandeAnnulation,
    db: AsyncSession = Depends(get_db)
):
    """Annuler une commande (partenaire ou livreur)"""
    query = select(Commande).where(Commande.id == commande_id)
    result = await db.execute(query)
    commande = result.scalar_one_or_none()
    
    if not commande:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Commande non trouvée"
        )
    
    # Vérifier que la commande peut être annulée
    non_annulable = [CommandeStatus.TERMINEE, CommandeStatus.ANNULEE]
    if commande.status in non_annulable:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cette commande ne peut plus être annulée"
        )
    
    # Si un livreur était assigné, le libérer
    if commande.livreur_id:
        livreur_query = select(Livreur).where(Livreur.id == commande.livreur_id)
        livreur_result = await db.execute(livreur_query)
        livreur = livreur_result.scalar_one_or_none()
        if livreur:
            # Vérifier s'il a d'autres courses actives
            other_active = select(func.count()).where(
                Commande.livreur_id == livreur.id,
                Commande.id != commande.id,
                Commande.status.in_([CommandeStatus.ACCEPTEE, CommandeStatus.EN_RECUPERATION, CommandeStatus.EN_LIVRAISON])
            )
            other_result = await db.execute(other_active)
            if (other_result.scalar() or 0) == 0:
                livreur.is_en_course = False
                livreur.is_disponible = True
    
    commande.status = CommandeStatus.ANNULEE
    commande.annulee_at = datetime.utcnow()
    commande.raison_annulation = annulation.raison
    
    await db.commit()
    await db.refresh(commande)
    
    # Notifier le livreur (si assigné) et le partenaire de l'annulation
    try:
        # Notifier le livreur
        if commande.livreur_id:
            livreur_q = select(Livreur).where(Livreur.id == commande.livreur_id)
            livreur_r = await db.execute(livreur_q)
            liv = livreur_r.scalar_one_or_none()
            if liv:
                token = await _get_user_device_token(db, liv.user_id)
                if token:
                    await notification_service.notifier_changement_status(
                        device_token=token,
                        status="ANNULEE",
                        numero_commande=commande.numero_commande,
                    )
        # Notifier le partenaire
        partenaire_q = select(Partenaire).where(Partenaire.id == commande.partenaire_id)
        partenaire_r = await db.execute(partenaire_q)
        partenaire_notif = partenaire_r.scalar_one_or_none()
        if partenaire_notif:
            token = await _get_user_device_token(db, partenaire_notif.user_id)
            if token:
                await notification_service.notifier_changement_status(
                    device_token=token,
                    status="ANNULEE",
                    numero_commande=commande.numero_commande,
                )
    except Exception as e:
        logger.warning(f"Notification annulation échouée: {e}")
    
    return commande


@router.post("/{commande_id}/evaluer", response_model=CommandeResponse)
async def evaluer_livreur(
    commande_id: str,
    evaluation: CommandeEvaluation,
    partenaire: Partenaire = Depends(get_current_partenaire),
    db: AsyncSession = Depends(get_db)
):
    """Évaluer le livreur après livraison (partenaire)"""
    query = select(Commande).where(
        Commande.id == commande_id,
        Commande.partenaire_id == partenaire.id,
        Commande.status == CommandeStatus.TERMINEE
    )
    result = await db.execute(query)
    commande = result.scalar_one_or_none()
    
    if not commande:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Commande non trouvée ou non terminée"
        )
    
    # Enregistrer l'évaluation
    commande.note_livreur = evaluation.note_livreur
    commande.commentaire_livreur = evaluation.commentaire_livreur
    
    # Mettre à jour la note du livreur
    if commande.livreur_id:
        livreur_query = select(Livreur).where(Livreur.id == commande.livreur_id)
        livreur_result = await db.execute(livreur_query)
        livreur = livreur_result.scalar_one_or_none()
        
        if livreur:
            total_notes = livreur.note_moyenne * livreur.nombre_evaluations
            livreur.nombre_evaluations += 1
            livreur.note_moyenne = (total_notes + evaluation.note_livreur) / livreur.nombre_evaluations
    
    await db.commit()
    await db.refresh(commande)
    
    return commande


@router.post("/{commande_id}/confirmer-paiement", response_model=CommandeResponse)
async def confirmer_paiement(
    commande_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Confirmer le paiement d'une commande (livreur confirme qu'il a reçu le cash)"""
    query = select(Commande).where(Commande.id == commande_id)
    result = await db.execute(query)
    commande = result.scalar_one_or_none()
    
    if not commande:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Commande non trouvée"
        )
    
    if commande.paiement_confirme == "oui":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le paiement a déjà été confirmé"
        )
    
    commande.paiement_confirme = "oui"
    await db.commit()
    await db.refresh(commande)
    
    return commande


@router.get("/{commande_id}", response_model=CommandeWithDetails)
async def get_commande_details(
    commande_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Obtenir les détails d'une commande"""
    query = select(Commande).where(Commande.id == commande_id)
    result = await db.execute(query)
    commande = result.scalar_one_or_none()
    
    if not commande:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Commande non trouvée"
        )
    
    # Récupérer les détails partenaire et livreur
    response_dict = CommandeResponse.model_validate(commande).model_dump()
    
    # Ajouter le partenaire
    if commande.partenaire_id:
        partenaire_query = select(Partenaire).where(Partenaire.id == commande.partenaire_id)
        partenaire_result = await db.execute(partenaire_query)
        partenaire = partenaire_result.scalar_one_or_none()
        if partenaire:
            response_dict["partenaire"] = {
                "nom": partenaire.nom,
                "adresse": partenaire.adresse
            }
    
    # Ajouter le livreur
    if commande.livreur_id:
        livreur_query = select(Livreur).where(Livreur.id == commande.livreur_id)
        livreur_result = await db.execute(livreur_query)
        livreur = livreur_result.scalar_one_or_none()
        if livreur:
            # Récupérer le téléphone depuis l'user
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
    
    return response_dict
