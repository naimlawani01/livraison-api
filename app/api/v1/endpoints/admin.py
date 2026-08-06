from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from typing import List, Literal, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field, field_validator
from ....core.database import get_db
from ....core.security import get_password_hash
from ....models.user import User, UserRole
from ....models.expediteur import Expediteur, TypeExpediteur
from ....models.livreur import Livreur
from ....models.course import Course, CourseStatus
from ....models.wallet_transaction import WalletTransaction
from ....models.credit_transaction import CreditTransaction
from ....utils.dependencies import get_current_admin
from ....services.notification_service import notification_service
from ....services import credit_service
from ....services.soldes import MontantInvalide
from ....services.storage_service import storage_service

router = APIRouter()

# ─────────────────────────────────────────────────────────────────────────────
# Test accounts (Apple App Review, internal QA)
# Phones MUST start with `+224600` (an unallocated GN prefix) so a leaked
# credential can't be used to impersonate a real user.
# ─────────────────────────────────────────────────────────────────────────────

TEST_ACCOUNT_PREFIX = "+224600"


class TestAccountCreate(BaseModel):
    phone: str = Field(..., description="Doit commencer par +224600")
    password: str = Field(..., min_length=6)
    role: Literal["EXPEDITEUR", "LIVREUR"]
    nom: str = Field(..., min_length=1)
    # Expediteur-only (defaults are fine for the Apple reviewer flow)
    type_expediteur: Optional[str] = "RESTAURANT"
    adresse: Optional[str] = "Conakry, Guinée"
    latitude: Optional[float] = 9.6412
    longitude: Optional[float] = -13.5784
    # Livreur-only
    type_vehicule: Optional[str] = "moto"
    solde_initial: Optional[float] = 0.0

    @field_validator("phone")
    @classmethod
    def must_be_test_prefix(cls, v: str) -> str:
        normalized = v.replace(" ", "")
        if not normalized.startswith(TEST_ACCOUNT_PREFIX):
            raise ValueError(
                f"Le numéro de test doit commencer par {TEST_ACCOUNT_PREFIX} "
                "(préfixe Guinée non alloué, sécurise contre l'usurpation)."
            )
        return normalized


@router.post("/test-accounts", status_code=status.HTTP_201_CREATED)
async def create_test_account(
    payload: TestAccountCreate,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Crée un compte test (Expediteur ou Livreur) avec mot de passe et
    is_verified=True d'office. Utilisé pour les credentials Apple Reviewer
    et les tests internes."""
    existing = await db.execute(select(User).where(User.phone == payload.phone))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Un utilisateur existe déjà avec le numéro {payload.phone}",
        )

    user = User(
        phone=payload.phone,
        password_hash=get_password_hash(payload.password),
        role=UserRole(payload.role),
        is_active=True,
        is_verified=True,
        last_login=None,
    )
    db.add(user)
    await db.flush()

    # `consent_accepted_at` is a naive `DateTime` column (no timezone) in
    # both Expediteur and Livreur models — strip tzinfo to avoid asyncpg
    # rejecting the mix of offset-aware datetime with offset-naive column.
    consent_naive = datetime.now(timezone.utc).replace(tzinfo=None)

    if payload.role == "EXPEDITEUR":
        try:
            type_p = TypeExpediteur(payload.type_expediteur or "AUTRE")
        except ValueError:
            type_p = TypeExpediteur.AUTRE
        expediteur = Expediteur(
            user_id=user.id,
            type_expediteur=type_p,
            nom=payload.nom,
            adresse=payload.adresse or "Conakry, Guinée",
            latitude=payload.latitude or 9.6412,
            longitude=payload.longitude or -13.5784,
            is_verified=True,
            is_open=True,
            consent_accepted_at=consent_naive,
            consent_version="apple-v1",
        )
        db.add(expediteur)
    else:  # LIVREUR
        livreur = Livreur(
            user_id=user.id,
            nom_complet=payload.nom,
            type_vehicule=payload.type_vehicule or "moto",
            is_verified=True,
            verified_at=datetime.now(timezone.utc),
            solde_disponible=payload.solde_initial or 0.0,
            total_gains=payload.solde_initial or 0.0,
            consent_accepted_at=consent_naive,
            consent_version="apple-v1",
        )
        db.add(livreur)

    await db.commit()
    await db.refresh(user)

    return {
        "message": "Compte test créé avec succès",
        "user_id": str(user.id),
        "phone": user.phone,
        "role": user.role,
        "credentials_for_apple_review": {
            "username": user.phone,
            "password": payload.password,
            "notes": (
                "Sönaiyaa uses phone + password authentication. "
                "Use the credentials above to sign in."
            ),
        },
    }


@router.get("/test-accounts")
async def list_test_accounts(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Liste tous les comptes test (numéros commençant par +224600)."""
    query = (
        select(User)
        .where(User.phone.like(f"{TEST_ACCOUNT_PREFIX}%"))
        .order_by(User.created_at.desc())
    )
    result = await db.execute(query)
    users = result.scalars().all()

    accounts = []
    for u in users:
        nom = "(inconnu)"
        if u.role == UserRole.EXPEDITEUR:
            pq = await db.execute(select(Expediteur).where(Expediteur.user_id == u.id))
            p = pq.scalar_one_or_none()
            if p:
                nom = p.nom
        elif u.role == UserRole.LIVREUR:
            lq = await db.execute(select(Livreur).where(Livreur.user_id == u.id))
            l = lq.scalar_one_or_none()
            if l:
                nom = l.nom_complet
        accounts.append({
            "user_id": str(u.id),
            "phone": u.phone,
            "role": u.role.value if isinstance(u.role, UserRole) else str(u.role),
            "nom": nom,
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        })
    return accounts


@router.delete("/test-accounts/{user_id}")
async def delete_test_account(
    user_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Supprime un compte test. Refuse si le numéro n'est pas un préfixe test."""
    query = select(User).where(User.id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="Compte non trouvé")
    if not user.phone.startswith(TEST_ACCOUNT_PREFIX):
        raise HTTPException(
            status_code=400,
            detail="Cet endpoint ne peut supprimer que les comptes test "
                   f"(préfixe {TEST_ACCOUNT_PREFIX}).",
        )

    if user.role == UserRole.LIVREUR:
        lq = await db.execute(select(Livreur).where(Livreur.user_id == user.id))
        livreur = lq.scalar_one_or_none()
        if livreur:
            await db.delete(livreur)
    elif user.role == UserRole.EXPEDITEUR:
        pq = await db.execute(select(Expediteur).where(Expediteur.user_id == user.id))
        expediteur = pq.scalar_one_or_none()
        if expediteur:
            await db.delete(expediteur)

    await db.delete(user)
    await db.commit()
    return {"message": "Compte test supprimé"}


@router.get("/stats")
async def get_platform_stats(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Obtenir les statistiques de la plateforme.

    Résultat caché 60s en Redis pour éviter les 11 requêtes d'agrégation
    sur chaque rafraîchissement de la page Dashboard admin. Si le cache
    est froid, on calcule et on stocke. Si Redis est down, on tombe sur
    le calcul direct sans cache (graceful degradation).
    """
    import json
    from ....core.redis import redis_client
    CACHE_KEY = "admin:stats:v1"
    CACHE_TTL = 60  # secondes

    try:
        cached = await redis_client.get(CACHE_KEY)
        if cached:
            return json.loads(cached if isinstance(cached, str) else cached.decode())
    except Exception:
        pass  # Redis down → on calcule sans cache

    # Nombre total d'utilisateurs
    users_query = select(func.count(User.id))
    users_result = await db.execute(users_query)
    total_users = users_result.scalar()
    
    # Nombre de expediteurs
    expediteurs_query = select(func.count(Expediteur.id))
    expediteurs_result = await db.execute(expediteurs_query)
    total_expediteurs = expediteurs_result.scalar()
    
    # Nombre de livreurs
    livreurs_query = select(func.count(Livreur.id))
    livreurs_result = await db.execute(livreurs_query)
    total_livreurs = livreurs_result.scalar()
    
    # Livreurs actifs (disponibles)
    livreurs_actifs_query = select(func.count(Livreur.id)).where(Livreur.is_disponible == True)
    livreurs_actifs_result = await db.execute(livreurs_actifs_query)
    livreurs_actifs = livreurs_actifs_result.scalar()
    
    # Courses totales
    courses_query = select(func.count(Course.id))
    courses_result = await db.execute(courses_query)
    total_courses = courses_result.scalar()
    
    # Courses en cours
    courses_en_cours_query = select(func.count(Course.id)).where(
        Course.status.in_([
            CourseStatus.DIFFUSEE,
            CourseStatus.ACCEPTEE,
            CourseStatus.EN_RECUPERATION,
            CourseStatus.EN_LIVRAISON
        ])
    )
    courses_en_cours_result = await db.execute(courses_en_cours_query)
    courses_en_cours = courses_en_cours_result.scalar()
    
    # Revenus totaux (commissions)
    revenus_query = select(func.sum(Course.commission_plateforme)).where(
        Course.status == CourseStatus.TERMINEE
    )
    revenus_result = await db.execute(revenus_query)
    revenus_totaux = revenus_result.scalar() or 0.0

    # Courses terminées
    terminees_query = select(func.count(Course.id)).where(Course.status == CourseStatus.TERMINEE)
    courses_terminees = (await db.execute(terminees_query)).scalar() or 0

    # Livreurs vérifiés
    verifies_query = select(func.count(Livreur.id)).where(Livreur.is_verified == True)
    livreurs_verifies = (await db.execute(verifies_query)).scalar() or 0

    # Retraits en attente
    retraits_query = select(func.count(WalletTransaction.id)).where(
        WalletTransaction.type == "retrait",
        WalletTransaction.statut == "en_attente"
    )
    retraits_en_attente = (await db.execute(retraits_query)).scalar() or 0

    # Taux de complétion
    taux_completion = round((courses_terminees / total_courses * 100), 1) if total_courses > 0 else 0.0

    result = {
        "total_utilisateurs": total_users,
        "total_expediteurs": total_expediteurs,
        "total_livreurs": total_livreurs,
        "livreurs_actifs": livreurs_actifs,
        "livreurs_verifies": livreurs_verifies,
        "total_courses": total_courses,
        "courses_en_cours": courses_en_cours,
        "courses_terminees": courses_terminees,
        "taux_completion": taux_completion,
        "revenus_totaux": revenus_totaux,
        "retraits_en_attente": retraits_en_attente,
    }

    # Cache le résultat 60s (silencieux si Redis down)
    try:
        await redis_client.set(CACHE_KEY, json.dumps(result, default=str), ex=CACHE_TTL)
    except Exception:
        pass

    return result


@router.get("/livreurs/en-attente", response_model=List[dict])
async def get_livreurs_en_attente(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Obtenir les livreurs en attente de validation"""
    from sqlalchemy.orm import selectinload

    query = select(Livreur).options(selectinload(Livreur.user)).where(Livreur.is_verified == False)
    result = await db.execute(query)
    livreurs = result.scalars().all()
    
    return [
        {
            "id": str(l.id),
            "nom_complet": l.nom_complet,
            "email": l.email,
            "phone": l.user.phone if l.user else None,
            "type_vehicule": l.type_vehicule,
            "plaque_immatriculation": l.plaque_immatriculation,
            "created_at": l.created_at,
            # Documents — URLs présignées temporaires (bucket privé)
            "piece_identite_url": storage_service.signed_view_url(l.piece_identite_url),
            "vehicule_doc_url": storage_service.signed_view_url(l.vehicule_doc_url),
            "vehicule_doc_type": l.vehicule_doc_type,
            "photo_profil_url": storage_service.signed_view_url(l.photo_profil_url),
            "docs_complets": bool(l.piece_identite_url and l.vehicule_doc_url and l.photo_profil_url),
        }
        for l in livreurs
    ]


@router.post("/livreurs/{livreur_id}/valider")
async def valider_livreur(
    livreur_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Valider le compte d'un livreur"""
    query = select(Livreur).where(Livreur.id == livreur_id)
    result = await db.execute(query)
    livreur = result.scalar_one_or_none()
    
    if not livreur:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Livreur non trouvé"
        )
    
    livreur.is_verified = True
    livreur.verified_at = datetime.now(timezone.utc)

    user_query = select(User).where(User.id == livreur.user_id)
    user_result = await db.execute(user_query)
    user = user_result.scalar_one_or_none()
    if user:
        user.is_verified = True

    await db.commit()
    return {"message": "Livreur validé avec succès"}


@router.post("/livreurs/{livreur_id}/rejeter")
async def rejeter_livreur(
    livreur_id: str,
    body: dict,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Rejeter le dossier d'un livreur avec motif."""
    query = select(Livreur).where(Livreur.id == livreur_id)
    result = await db.execute(query)
    livreur = result.scalar_one_or_none()
    if not livreur:
        raise HTTPException(status_code=404, detail="Livreur non trouvé")

    # Effacer les documents pour que le livreur puisse les renvoyer
    livreur.piece_identite_url = None
    livreur.vehicule_doc_url = None
    livreur.vehicule_doc_type = None
    livreur.photo_profil_url = None
    await db.commit()

    motif = body.get("motif", "")
    if livreur.device_token:
        msg = f"Motif : {motif}" if motif else "Veuillez renvoyer vos documents."
        await notification_service.envoyer_notification_push(
            livreur.device_token,
            titre="Dossier non validé",
            message=msg,
            data={"type": "dossier_rejete", "motif": motif},
        )
    return {"message": "Dossier rejeté", "motif": motif}


@router.post("/livreurs/{livreur_id}/suspendre")
async def suspendre_livreur(
    livreur_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Suspendre un livreur"""
    query = select(Livreur).where(Livreur.id == livreur_id)
    result = await db.execute(query)
    livreur = result.scalar_one_or_none()
    
    if not livreur:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Livreur non trouvé"
        )
    
    # Récupérer l'utilisateur associé
    user_query = select(User).where(User.id == livreur.user_id)
    user_result = await db.execute(user_query)
    user = user_result.scalar_one_or_none()
    
    if user:
        user.is_active = False
        livreur.is_disponible = False
    
    await db.commit()
    
    return {"message": "Livreur suspendu"}


@router.get("/livreurs/tous", response_model=List[dict])
async def get_tous_livreurs(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Obtenir les livreurs (paginé — cap à 200 par défaut, max 500).

    Les params `?limit=` et `?offset=` permettent à l'admin-web de paginer
    quand la base atteint plusieurs centaines de livreurs.
    """
    from sqlalchemy.orm import selectinload

    query = (
        select(Livreur)
        .options(selectinload(Livreur.user))
        .order_by(Livreur.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(query)
    livreurs = result.scalars().all()
    
    return [
        {
            "id": str(l.id),
            "nom_complet": l.nom_complet,
            "email": l.email,
            "phone": l.user.phone if l.user else None,
            "type_vehicule": l.type_vehicule,
            "is_disponible": l.is_disponible,
            "is_verified": l.is_verified,
            "note_moyenne": l.note_moyenne,
            "nombre_courses_completees": l.nombre_courses_completees,
            "total_gains": l.total_gains,
            "solde_disponible": l.solde_disponible,
            "plaque_immatriculation": l.plaque_immatriculation,
            "created_at": l.created_at,
        }
        for l in livreurs
    ]


@router.get("/livreurs/{livreur_id}", response_model=dict)
async def get_livreur_detail(
    livreur_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Obtenir le détail complet d'un livreur"""
    from sqlalchemy.orm import selectinload

    query = select(Livreur).options(selectinload(Livreur.user)).where(Livreur.id == livreur_id)
    result = await db.execute(query)
    l = result.scalar_one_or_none()

    if not l:
        raise HTTPException(status_code=404, detail="Livreur non trouvé")

    return {
        "id": str(l.id),
        "nom_complet": l.nom_complet,
        "email": l.email,
        "phone": l.user.phone if l.user else None,
        "type_vehicule": l.type_vehicule,
        "plaque_immatriculation": l.plaque_immatriculation,
        "marque_modele": l.marque_modele,
        "is_disponible": l.is_disponible,
        "is_en_course": l.is_en_course,
        "is_verified": l.is_verified,
        "note_moyenne": l.note_moyenne,
        "nombre_courses_completees": l.nombre_courses_completees,
        "nombre_evaluations": l.nombre_evaluations,
        "total_gains": l.total_gains,
        "solde_disponible": l.solde_disponible,
        "piece_identite_url": storage_service.signed_view_url(l.piece_identite_url),
        "vehicule_doc_url": storage_service.signed_view_url(l.vehicule_doc_url),
        "vehicule_doc_type": l.vehicule_doc_type,
        "photo_profil_url": storage_service.signed_view_url(l.photo_profil_url),
        "docs_complets": bool(l.piece_identite_url and l.vehicule_doc_url and l.photo_profil_url),
        "created_at": l.created_at,
    }


@router.get("/expediteurs/en-attente", response_model=List[dict])
async def get_expediteurs_en_attente(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Obtenir les expediteurs en attente de validation"""
    from sqlalchemy.orm import selectinload

    query = select(Expediteur).options(selectinload(Expediteur.user)).where(Expediteur.is_verified == False)
    result = await db.execute(query)
    expediteurs = result.scalars().all()
    
    return [
        {
            "id": str(r.id),
            "nom": r.nom,
            "adresse": r.adresse,
            "email": r.email,
            "phone": r.user.phone if r.user else None,
            "type_expediteur": r.type_expediteur,
            "created_at": r.created_at,
            # Document — URL présignée temporaire (bucket privé)
            "devanture_url": storage_service.signed_view_url(r.devanture_url),
            "docs_complets": bool(r.devanture_url),
        }
        for r in expediteurs
    ]


@router.post("/expediteurs/{expediteur_id}/valider")
async def valider_expediteur(
    expediteur_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Valider le compte d'un expediteur"""
    query = select(Expediteur).where(Expediteur.id == expediteur_id)
    result = await db.execute(query)
    expediteur = result.scalar_one_or_none()
    
    if not expediteur:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Expediteur non trouvé"
        )
    
    expediteur.is_verified = True

    user_query = select(User).where(User.id == expediteur.user_id)
    user_result = await db.execute(user_query)
    user = user_result.scalar_one_or_none()
    if user:
        user.is_verified = True

    await db.commit()
    return {"message": "Expediteur validé avec succès"}


@router.post("/expediteurs/{expediteur_id}/rejeter")
async def rejeter_expediteur(
    expediteur_id: str,
    body: dict,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Rejeter le dossier d'un expediteur avec motif."""
    query = select(Expediteur).where(Expediteur.id == expediteur_id)
    result = await db.execute(query)
    expediteur = result.scalar_one_or_none()
    if not expediteur:
        raise HTTPException(status_code=404, detail="Expediteur non trouvé")

    expediteur.devanture_url = None
    expediteur.rccm_url = None
    await db.commit()

    motif = body.get("motif", "")
    user_q = await db.execute(select(User).where(User.id == expediteur.user_id))
    user = user_q.scalar_one_or_none()
    if user and user.device_token:
        msg = f"Motif : {motif}" if motif else "Veuillez renvoyer vos documents."
        await notification_service.envoyer_notification_push(
            user.device_token,
            titre="Dossier non validé",
            message=msg,
            data={"type": "dossier_rejete", "motif": motif},
        )
    return {"message": "Dossier rejeté", "motif": motif}


@router.post("/expediteurs/{expediteur_id}/suspendre")
async def suspendre_expediteur(
    expediteur_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Suspendre un expediteur"""
    query = select(Expediteur).where(Expediteur.id == expediteur_id)
    result = await db.execute(query)
    expediteur = result.scalar_one_or_none()
    
    if not expediteur:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Expediteur non trouvé"
        )
    
    expediteur.is_verified = False
    
    user_query = select(User).where(User.id == expediteur.user_id)
    user_result = await db.execute(user_query)
    user = user_result.scalar_one_or_none()
    if user:
        user.is_active = False
    
    await db.commit()
    
    return {"message": "Expediteur suspendu"}


@router.get("/expediteurs/tous", response_model=List[dict])
async def get_tous_expediteurs(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Obtenir les expediteurs (paginé — cap à 200 par défaut, max 500)."""
    from sqlalchemy.orm import selectinload

    query = (
        select(Expediteur)
        .options(selectinload(Expediteur.user))
        .order_by(Expediteur.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(query)
    expediteurs = result.scalars().all()
    
    return [
        {
            "id": str(r.id),
            "nom": r.nom,
            "adresse": r.adresse,
            "email": r.email,
            "phone": r.user.phone if r.user else None,
            "type_expediteur": r.type_expediteur,
            "is_open": r.is_open,
            "is_verified": r.is_verified,
            "note_moyenne": r.note_moyenne,
            "nombre_evaluations": r.nombre_evaluations,
            "created_at": r.created_at,
        }
        for r in expediteurs
    ]


@router.get("/expediteurs/{expediteur_id}", response_model=dict)
async def get_expediteur_detail(
    expediteur_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Obtenir le détail complet d'un expediteur"""
    from sqlalchemy.orm import selectinload

    query = select(Expediteur).options(selectinload(Expediteur.user)).where(Expediteur.id == expediteur_id)
    result = await db.execute(query)
    r = result.scalar_one_or_none()

    if not r:
        raise HTTPException(status_code=404, detail="Expediteur non trouvé")

    courses_count = await db.execute(
        select(func.count(Course.id)).where(
            Course.expediteur_id == r.id,
            Course.status == CourseStatus.TERMINEE
        )
    )
    nombre_courses_terminees = courses_count.scalar() or 0

    return {
        "id": str(r.id),
        "nom": r.nom,
        "description": r.description,
        "adresse": r.adresse,
        "type_expediteur": r.type_expediteur,
        "email": r.email,
        "phone": r.user.phone if r.user else None,
        "telephone_secondaire": r.telephone_secondaire,
        "latitude": r.latitude,
        "longitude": r.longitude,
        "is_open": r.is_open,
        "is_verified": r.is_verified,
        "note_moyenne": r.note_moyenne,
        "nombre_evaluations": r.nombre_evaluations,
        "nombre_courses_terminees": nombre_courses_terminees,
        "devanture_url": storage_service.signed_view_url(r.devanture_url),
        "docs_complets": bool(r.devanture_url),
        "horaires": r.horaires,
        "created_at": r.created_at,
    }


@router.get("/courses/recentes", response_model=List[dict])
async def get_courses_recentes(
    admin: User = Depends(get_current_admin),
    limit: int = 20,
    db: AsyncSession = Depends(get_db)
):
    """Obtenir les courses récentes avec infos expediteur et livreur"""
    from sqlalchemy.orm import selectinload

    query = (
        select(Course)
        .options(
            selectinload(Course.expediteur).selectinload(Expediteur.user),
            selectinload(Course.livreur).selectinload(Livreur.user),
        )
        .order_by(Course.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    courses = result.scalars().all()

    items = []
    for c in courses:
        expediteur_nom = c.expediteur.nom if c.expediteur else None
        expediteur_phone = c.expediteur.user.phone if c.expediteur and c.expediteur.user else None
        livreur_nom = c.livreur.nom_complet if c.livreur else None
        livreur_phone = c.livreur.user.phone if c.livreur and c.livreur.user else None

        items.append({
            "id": str(c.id),
            "numero_course": c.numero_course,
            "status": c.status,
            "prix_propose": c.prix_propose,
            "contact_client_nom": c.contact_client_nom,
            "contact_client_telephone": c.contact_client_telephone,
            "expediteur_nom": expediteur_nom,
            "expediteur_phone": expediteur_phone,
            "livreur_nom": livreur_nom,
            "livreur_phone": livreur_phone,
            "mode_paiement": c.mode_paiement,
            "paiement_confirme": c.paiement_confirme,
            "geniuspay_checkout_url": c.geniuspay_checkout_url,
            "commission_plateforme": c.commission_plateforme,
            "distance_km": c.distance_km,
            "created_at": c.created_at,
        })

    return items


# ── Retraits wallet ───────────────────────────────────────────────────────────

@router.get("/wallet/retraits")
async def get_retraits_en_attente(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Lister les demandes de retrait en attente"""
    query = (
        select(WalletTransaction, Livreur)
        .join(Livreur, WalletTransaction.livreur_id == Livreur.id)
        .where(
            WalletTransaction.type == "retrait",
            WalletTransaction.statut == "en_attente"
        )
        .order_by(WalletTransaction.created_at.desc())
    )
    result = await db.execute(query)
    rows = result.all()

    items = []
    for txn, livreur in rows:
        user_q = select(User).where(User.id == livreur.user_id)
        user_r = await db.execute(user_q)
        user = user_r.scalar_one_or_none()
        items.append({
            "id": str(txn.id),
            "livreur_id": str(livreur.id),
            "livreur_nom": livreur.nom_complet,
            "livreur_phone": user.phone if user else None,
            "montant": txn.montant,
            "description": txn.description,
            "solde_avant": txn.solde_avant,
            "solde_apres": txn.solde_apres,
            "created_at": txn.created_at,
        })
    return items


@router.post("/wallet/retraits/{txn_id}/valider")
async def valider_retrait(
    txn_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Valider une demande de retrait (marquer comme complète)"""
    query = select(WalletTransaction).where(WalletTransaction.id == txn_id)
    result = await db.execute(query)
    txn = result.scalar_one_or_none()
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction non trouvée")
    if txn.statut != "en_attente":
        raise HTTPException(status_code=400, detail="Cette demande a déjà été traitée")
    txn.statut = "complete"
    await db.commit()
    return {"message": "Retrait validé"}


@router.post("/wallet/retraits/{txn_id}/rejeter")
async def rejeter_retrait(
    txn_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Rejeter une demande de retrait et rembourser le livreur"""
    query = select(WalletTransaction).where(WalletTransaction.id == txn_id)
    result = await db.execute(query)
    txn = result.scalar_one_or_none()
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction non trouvée")
    if txn.statut != "en_attente":
        raise HTTPException(status_code=400, detail="Cette demande a déjà été traitée")
    # Rembourser le solde
    livreur_q = select(Livreur).where(Livreur.id == txn.livreur_id)
    livreur_r = await db.execute(livreur_q)
    livreur = livreur_r.scalar_one_or_none()
    if livreur:
        livreur.solde_disponible += txn.montant
        if livreur.device_token:
            montant_fmt = f"{int(txn.montant):,}".replace(",", " ")
            await notification_service.envoyer_notification_push(
                livreur.device_token,
                titre="Retrait refusé",
                message=f"Votre demande de {montant_fmt} GNF a été refusée. Le solde a été recrédité.",
                data={"type": "retrait_refuse"},
            )
    txn.statut = "refuse"
    await db.commit()
    return {"message": "Retrait refusé — solde remboursé au livreur"}


# ── Gestion des utilisateurs ──────────────────────────────────────────────────

@router.get("/users")
async def get_tous_users(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
    role: str = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Lister les utilisateurs avec filtre optionnel par rôle (paginé — cap 200, max 500)."""
    query = select(User).order_by(User.created_at.desc())
    if role:
        query = query.where(User.role == role)
    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    users = result.scalars().all()

    items = []
    for u in users:
        # Récupérer le nom selon le rôle
        nom = None
        if u.role == "LIVREUR":
            lq = select(Livreur).where(Livreur.user_id == u.id)
            lr = await db.execute(lq)
            livreur = lr.scalar_one_or_none()
            nom = livreur.nom_complet if livreur else None
        elif u.role == "EXPEDITEUR":
            pq = select(Expediteur).where(Expediteur.user_id == u.id)
            pr = await db.execute(pq)
            expediteur = pr.scalar_one_or_none()
            nom = expediteur.nom if expediteur else None

        items.append({
            "id": str(u.id),
            "phone": u.phone,
            "role": u.role,
            "nom": nom,
            "is_active": u.is_active,
            "is_verified": u.is_verified,
            "created_at": u.created_at,
            "last_login": u.last_login,
        })

    return items


@router.post("/users/{user_id}/suspendre")
async def suspendre_user(
    user_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Activer ou suspendre un compte utilisateur"""
    query = select(User).where(User.id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur non trouvé")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Impossible de suspendre votre propre compte")
    if user.role == "ADMIN":
        raise HTTPException(status_code=403, detail="Impossible de suspendre un autre administrateur")

    user.is_active = not user.is_active
    await db.commit()

    action = "réactivé" if user.is_active else "suspendu"
    return {"message": f"Utilisateur {action}", "is_active": user.is_active}


@router.delete("/users/{user_id}")
async def supprimer_user(
    user_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Supprimer définitivement un utilisateur et son profil associé"""
    query = select(User).where(User.id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur non trouvé")
    if str(user.id) == str(admin.id):
        raise HTTPException(status_code=400, detail="Impossible de supprimer votre propre compte")
    if user.role == "ADMIN":
        raise HTTPException(status_code=403, detail="Impossible de supprimer un administrateur")

    # Supprimer le profil associé
    if user.role == "LIVREUR":
        lq = select(Livreur).where(Livreur.user_id == user.id)
        lr = await db.execute(lq)
        livreur = lr.scalar_one_or_none()
        if livreur:
            await db.delete(livreur)
    elif user.role == "EXPEDITEUR":
        pq = select(Expediteur).where(Expediteur.user_id == user.id)
        pr = await db.execute(pq)
        expediteur = pr.scalar_one_or_none()
        if expediteur:
            await db.delete(expediteur)

    await db.delete(user)
    await db.commit()
    return {"message": "Utilisateur supprimé définitivement"}


# ── Crédit expéditeur (gestion admin) ─────────────────────────────────────────

class CreditManuelRequest(BaseModel):
    montant: float = Field(..., gt=0, description="Montant à créditer (GNF)")
    motif: Optional[str] = Field(None, max_length=255, description="Raison du crédit manuel")


@router.post("/expediteurs/{expediteur_id}/credit", status_code=status.HTTP_201_CREATED)
async def crediter_expediteur(
    expediteur_id: str,
    body: CreditManuelRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Créditer manuellement le Crédit d'un expéditeur (correction, geste commercial,
    confirmation Mobile Money hors PSP)."""
    p = (await db.execute(
        select(Expediteur).where(Expediteur.id == expediteur_id)
    )).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expéditeur introuvable")

    try:
        txn = await credit_service.crediter_admin(db, expediteur_id, body.montant, motif=body.motif)
    except MontantInvalide as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return {
        "message": "Crédit ajouté",
        "montant_credite": body.montant,
        "credit_solde": txn.solde_apres,
        "transaction_id": str(txn.id),
    }


@router.get("/expediteurs/{expediteur_id}/credit")
async def get_expediteur_credit(
    expediteur_id: str,
    limit: int = 30,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Solde de Crédit d'un expéditeur + derniers mouvements (supervision)."""
    p = (await db.execute(
        select(Expediteur).where(Expediteur.id == expediteur_id)
    )).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expéditeur introuvable")

    txns = (await db.execute(
        select(CreditTransaction)
        .where(CreditTransaction.expediteur_id == p.id)
        .order_by(CreditTransaction.created_at.desc())
        .limit(min(limit, 200))
    )).scalars().all()

    return {
        "expediteur_id": str(p.id),
        "nom": p.nom,
        "credit_solde": round(p.credit_solde or 0.0, 2),
        "transactions": [
            {
                "id": str(t.id),
                "type": t.type,
                "montant": t.montant,
                "solde_avant": t.solde_avant,
                "solde_apres": t.solde_apres,
                "description": t.description,
                "course_id": str(t.course_id) if t.course_id else None,
                "statut": t.statut,
                "created_at": t.created_at.isoformat(),
            }
            for t in txns
        ],
    }
