from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List
from datetime import datetime, timezone
from ....core.database import get_db
from ....models.user import User
from ....models.partenaire import Partenaire
from ....models.livreur import Livreur
from ....models.commande import Commande, CommandeStatus
from ....utils.dependencies import get_current_admin

router = APIRouter()


@router.get("/stats")
async def get_platform_stats(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Obtenir les statistiques de la plateforme"""
    # Nombre total d'utilisateurs
    users_query = select(func.count(User.id))
    users_result = await db.execute(users_query)
    total_users = users_result.scalar()
    
    # Nombre de partenaires
    partenaires_query = select(func.count(Partenaire.id))
    partenaires_result = await db.execute(partenaires_query)
    total_partenaires = partenaires_result.scalar()
    
    # Nombre de livreurs
    livreurs_query = select(func.count(Livreur.id))
    livreurs_result = await db.execute(livreurs_query)
    total_livreurs = livreurs_result.scalar()
    
    # Livreurs actifs (disponibles)
    livreurs_actifs_query = select(func.count(Livreur.id)).where(Livreur.is_disponible == True)
    livreurs_actifs_result = await db.execute(livreurs_actifs_query)
    livreurs_actifs = livreurs_actifs_result.scalar()
    
    # Commandes totales
    commandes_query = select(func.count(Commande.id))
    commandes_result = await db.execute(commandes_query)
    total_commandes = commandes_result.scalar()
    
    # Commandes en cours
    commandes_en_cours_query = select(func.count(Commande.id)).where(
        Commande.status.in_([
            CommandeStatus.DIFFUSEE,
            CommandeStatus.ACCEPTEE,
            CommandeStatus.EN_RECUPERATION,
            CommandeStatus.EN_LIVRAISON
        ])
    )
    commandes_en_cours_result = await db.execute(commandes_en_cours_query)
    commandes_en_cours = commandes_en_cours_result.scalar()
    
    # Revenus totaux (commissions)
    revenus_query = select(func.sum(Commande.commission_plateforme)).where(
        Commande.status == CommandeStatus.TERMINEE
    )
    revenus_result = await db.execute(revenus_query)
    revenus_totaux = revenus_result.scalar() or 0.0
    
    return {
        "total_utilisateurs": total_users,
        "total_partenaires": total_partenaires,
        "total_livreurs": total_livreurs,
        "livreurs_actifs": livreurs_actifs,
        "total_commandes": total_commandes,
        "commandes_en_cours": commandes_en_cours,
        "revenus_totaux": revenus_totaux
    }


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
            "created_at": l.created_at
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
    db: AsyncSession = Depends(get_db)
):
    """Obtenir tous les livreurs"""
    from sqlalchemy.orm import selectinload

    query = select(Livreur).options(selectinload(Livreur.user)).order_by(Livreur.created_at.desc())
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
            "created_at": l.created_at,
        }
        for l in livreurs
    ]


@router.get("/partenaires/en-attente", response_model=List[dict])
async def get_partenaires_en_attente(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Obtenir les partenaires en attente de validation"""
    from sqlalchemy.orm import selectinload

    query = select(Partenaire).options(selectinload(Partenaire.user)).where(Partenaire.is_verified == False)
    result = await db.execute(query)
    partenaires = result.scalars().all()
    
    return [
        {
            "id": str(r.id),
            "nom": r.nom,
            "adresse": r.adresse,
            "email": r.email,
            "phone": r.user.phone if r.user else None,
            "type_partenaire": r.type_partenaire,
            "created_at": r.created_at,
        }
        for r in partenaires
    ]


@router.post("/partenaires/{partenaire_id}/valider")
async def valider_partenaire(
    partenaire_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Valider le compte d'un partenaire"""
    query = select(Partenaire).where(Partenaire.id == partenaire_id)
    result = await db.execute(query)
    partenaire = result.scalar_one_or_none()
    
    if not partenaire:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Partenaire non trouvé"
        )
    
    partenaire.is_verified = True
    
    user_query = select(User).where(User.id == partenaire.user_id)
    user_result = await db.execute(user_query)
    user = user_result.scalar_one_or_none()
    if user:
        user.is_verified = True
    
    await db.commit()
    
    return {"message": "Partenaire validé avec succès"}


@router.post("/partenaires/{partenaire_id}/suspendre")
async def suspendre_partenaire(
    partenaire_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Suspendre un partenaire"""
    query = select(Partenaire).where(Partenaire.id == partenaire_id)
    result = await db.execute(query)
    partenaire = result.scalar_one_or_none()
    
    if not partenaire:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Partenaire non trouvé"
        )
    
    partenaire.is_verified = False
    
    user_query = select(User).where(User.id == partenaire.user_id)
    user_result = await db.execute(user_query)
    user = user_result.scalar_one_or_none()
    if user:
        user.is_active = False
    
    await db.commit()
    
    return {"message": "Partenaire suspendu"}


@router.get("/partenaires/tous", response_model=List[dict])
async def get_tous_partenaires(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Obtenir tous les partenaires"""
    from sqlalchemy.orm import selectinload

    query = select(Partenaire).options(selectinload(Partenaire.user)).order_by(Partenaire.created_at.desc())
    result = await db.execute(query)
    partenaires = result.scalars().all()
    
    return [
        {
            "id": str(r.id),
            "nom": r.nom,
            "adresse": r.adresse,
            "email": r.email,
            "phone": r.user.phone if r.user else None,
            "type_partenaire": r.type_partenaire,
            "is_open": r.is_open,
            "is_verified": r.is_verified,
            "note_moyenne": r.note_moyenne,
            "nombre_evaluations": r.nombre_evaluations,
            "created_at": r.created_at,
        }
        for r in partenaires
    ]


@router.get("/commandes/recentes", response_model=List[dict])
async def get_commandes_recentes(
    admin: User = Depends(get_current_admin),
    limit: int = 20,
    db: AsyncSession = Depends(get_db)
):
    """Obtenir les commandes récentes avec infos partenaire et livreur"""
    from sqlalchemy.orm import selectinload

    query = (
        select(Commande)
        .options(
            selectinload(Commande.partenaire).selectinload(Partenaire.user),
            selectinload(Commande.livreur).selectinload(Livreur.user),
        )
        .order_by(Commande.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    commandes = result.scalars().all()

    items = []
    for c in commandes:
        partenaire_nom = c.partenaire.nom if c.partenaire else None
        partenaire_phone = c.partenaire.user.phone if c.partenaire and c.partenaire.user else None
        livreur_nom = c.livreur.nom_complet if c.livreur else None
        livreur_phone = c.livreur.user.phone if c.livreur and c.livreur.user else None

        items.append({
            "id": str(c.id),
            "numero_commande": c.numero_commande,
            "status": c.status,
            "prix_propose": c.prix_propose,
            "contact_client_nom": c.contact_client_nom,
            "contact_client_telephone": c.contact_client_telephone,
            "partenaire_nom": partenaire_nom,
            "partenaire_phone": partenaire_phone,
            "livreur_nom": livreur_nom,
            "livreur_phone": livreur_phone,
            "created_at": c.created_at,
        })

    return items
