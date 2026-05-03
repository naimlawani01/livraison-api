from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from datetime import datetime, timezone
from ....core.database import get_db
from ....models.livreur import Livreur
from ....models.user import User
from ....schemas.livreur import (
    LivreurCreate,
    LivreurUpdate,
    LivreurResponse,
    LivreurLocationUpdate,
    LivreurDisponibiliteUpdate
)
from sqlalchemy.orm import selectinload
from ....utils.dependencies import get_current_user, get_current_livreur
from ....services.storage_service import storage_service
from ....services.notification_service import notification_service
from ....models.commande import Commande, CommandeStatus
from ....core.config import settings
from haversine import haversine

router = APIRouter()


@router.post("/", response_model=LivreurResponse, status_code=status.HTTP_201_CREATED)
async def create_livreur(
    livreur_data: LivreurCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Créer le profil livreur (après inscription)"""
    # Vérifier qu'il n'a pas déjà un profil
    query = select(Livreur).where(Livreur.user_id == current_user.id)
    result = await db.execute(query)
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Profil livreur déjà existant"
        )
    
    # Créer le livreur
    livreur = Livreur(
        user_id=current_user.id,
        nom_complet=livreur_data.nom_complet,
        email=livreur_data.email,
        type_vehicule=livreur_data.type_vehicule,
        marque_modele=livreur_data.marque_modele,
        plaque_immatriculation=livreur_data.plaque_immatriculation
    )
    
    db.add(livreur)
    await db.commit()
    await db.refresh(livreur)
    
    return livreur


@router.get("/me", response_model=LivreurResponse)
async def get_my_profile(
    livreur: Livreur = Depends(get_current_livreur)
):
    """Obtenir mon profil livreur"""
    return livreur


@router.patch("/me", response_model=LivreurResponse)
async def update_my_profile(
    livreur_data: LivreurUpdate,
    livreur: Livreur = Depends(get_current_livreur),
    db: AsyncSession = Depends(get_db)
):
    """Mettre à jour mon profil livreur"""
    update_dict = livreur_data.model_dump(exclude_unset=True)
    
    for key, value in update_dict.items():
        setattr(livreur, key, value)
    
    await db.commit()
    await db.refresh(livreur)
    
    return livreur


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_account(
    livreur: Livreur = Depends(get_current_livreur),
    db: AsyncSession = Depends(get_db)
):
    """Supprimer mon compte (droit à l’effacement RGPD)"""
    # Anonymiser les données personnelles plutôt que supprimer
    # pour conserver l'historique comptable
    livreur.nom_complet = "Compte supprimé"
    livreur.email = None
    livreur.piece_identite_url = None
    livreur.permis_conduire_url = None
    livreur.photo_profil_url = None
    livreur.plaque_immatriculation = None
    livreur.latitude = None
    livreur.longitude = None
    livreur.device_token = None
    livreur.is_disponible = False

    # Supprimer le user (cascade supprime la session)
    user_query = select(User).where(User.id == livreur.user_id)
    result = await db.execute(user_query)
    user = result.scalar_one_or_none()
    if user:
        user.phone = f"deleted_{livreur.id}"
        user.password_hash = "deleted"

    await db.commit()


from ....core.redis import redis_client
import logging

logger = logging.getLogger(__name__)

@router.patch("/me/location", response_model=LivreurResponse)
async def update_my_location(
    location_data: LivreurLocationUpdate,
    livreur: Livreur = Depends(get_current_livreur),
    db: AsyncSession = Depends(get_db)
):
    """Mettre à jour ma position GPS"""
    # 1. Toujours mettre à jour le Cache Rapide Redis (Temps réel)
    try:
        # geoadd prend un tuple (longitude, latitude, nom)
        await redis_client.geoadd(
            "livreurs_locations",
            (location_data.longitude, location_data.latitude, str(livreur.id))
        )
    except Exception as e:
        logger.warning(f"Impossible de mettre à jour le cache GPS Redis : {e}")

    # 2. Sauvegarder dans PostgreSQL seulement toutes les 2 minutes pour protéger la base
    now = datetime.now(timezone.utc)
    do_postgres_update = True
    
    if livreur.derniere_position_maj:
        delta = (now - livreur.derniere_position_maj).total_seconds()
        if delta < 120:  # Moins de 2 minutes
            do_postgres_update = False
            
    if do_postgres_update:
        livreur.latitude = location_data.latitude
        livreur.longitude = location_data.longitude
        livreur.derniere_position_maj = now
        
        await db.commit()
        await db.refresh(livreur)
    else:
        # Mettre l'objet à jour en mémoire pour la réponse sans commit
        livreur.latitude = location_data.latitude
        livreur.longitude = location_data.longitude
    
    return livreur


@router.patch("/me/disponibilite", response_model=LivreurResponse)
async def update_disponibilite(
    disponibilite_data: LivreurDisponibiliteUpdate,
    livreur: Livreur = Depends(get_current_livreur),
    db: AsyncSession = Depends(get_db)
):
    """Activer/désactiver ma disponibilité"""
    # Vérifier que le livreur est vérifié
    if not livreur.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Votre compte doit être vérifié par un administrateur"
        )

    # Vérifier que la position est connue si activation
    if disponibilite_data.is_disponible and (not livreur.latitude or not livreur.longitude):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Veuillez d'abord mettre à jour votre position"
        )

    livreur.is_disponible = disponibilite_data.is_disponible

    await db.commit()
    await db.refresh(livreur)

    if not disponibilite_data.is_disponible:
        # Retirer du GEO index Redis
        try:
            await redis_client.zrem("livreurs_locations", str(livreur.id))
        except Exception as e:
            logger.warning(f"Impossible de retirer le livreur de Redis GEO : {e}")
    else:
        # Le livreur vient de passer en ligne — lui notifier les courses déjà diffusées
        # dans son rayon pour qu'il ne rate pas une commande créée pendant son absence.
        if livreur.device_token and livreur.latitude and livreur.longitude:
            try:
                result = await db.execute(
                    select(Commande)
                    .where(Commande.status == CommandeStatus.DIFFUSEE)
                    .options(selectinload(Commande.partenaire))
                )
                courses_en_attente = result.scalars().all()
                pos_livreur = (livreur.latitude, livreur.longitude)
                rayon = settings.MAX_SEARCH_RADIUS_KM

                for commande in courses_en_attente:
                    p = commande.partenaire
                    if p and p.latitude and p.longitude:
                        dist = haversine(pos_livreur, (p.latitude, p.longitude))
                        if dist <= rayon:
                            await notification_service.notifier_nouvelle_commande(
                                device_tokens=[livreur.device_token],
                                numero_commande=commande.numero_commande,
                                partenaire_nom=p.nom,
                                prix=commande.prix_propose or 0,
                                distance_km=round(dist, 1),
                            )
            except Exception as e:
                logger.warning(f"Impossible d'envoyer les courses en attente au livreur {livreur.id}: {e}")

    return livreur


@router.get("/", response_model=List[LivreurResponse])
async def list_livreurs(
    skip: int = 0,
    limit: int = 50,
    disponible_only: bool = False,
    db: AsyncSession = Depends(get_db)
):
    """Lister les livreurs (pour admin)"""
    query = select(Livreur)
    
    if disponible_only:
        query = query.where(Livreur.is_disponible == True)
    
    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    livreurs = result.scalars().all()
    
    return livreurs


@router.get("/{livreur_id}", response_model=LivreurResponse)
async def get_livreur(
    livreur_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Obtenir un livreur par ID"""
    query = select(Livreur).where(Livreur.id == livreur_id)
    result = await db.execute(query)
    livreur = result.scalar_one_or_none()
    
    if not livreur:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Livreur non trouvé"
        )
    
    return livreur


@router.post("/upload-document")
async def upload_document(
    document_type: str = Form(...),
    vehicule_doc_type: Optional[str] = Form(None),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Uploader un document vers Cloudflare R2.
    document_type : "piece_identite" | "vehicule_doc" | "photo_profil"
    vehicule_doc_type (requis si document_type=vehicule_doc) : "permis_conduire" | "carte_grise"
    """
    valid_types = ["piece_identite", "vehicule_doc", "photo_profil"]
    if document_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"Type invalide. Choisissez parmi : {', '.join(valid_types)}")

    if document_type == "vehicule_doc":
        if vehicule_doc_type not in ("permis_conduire", "carte_grise"):
            raise HTTPException(status_code=400, detail="vehicule_doc_type doit être 'permis_conduire' ou 'carte_grise'")

    query = select(Livreur).where(Livreur.user_id == current_user.id)
    result = await db.execute(query)
    livreur = result.scalar_one_or_none()
    if not livreur:
        raise HTTPException(status_code=404, detail="Profil livreur non trouvé")

    content_type = file.content_type or "application/octet-stream"
    allowed_types = {
        "image/jpeg", "image/jpg", "image/png", "image/heic", "image/heif", "image/webp",
        "application/pdf",
    }
    if content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Format non supporté. Utilisez une image (JPG, PNG, HEIC) ou un PDF.")

    content = await file.read()
    ext_default = "pdf" if content_type == "application/pdf" else "jpg"
    url = await storage_service.upload_document(
        file_data=content,
        folder="livreurs",
        original_filename=file.filename or f"{document_type}.{ext_default}",
        content_type=content_type,
    )

    if document_type == "vehicule_doc":
        livreur.vehicule_doc_url = url
        livreur.vehicule_doc_type = vehicule_doc_type
    else:
        setattr(livreur, f"{document_type}_url", url)

    await db.commit()
    return {"message": "Document uploadé avec succès", "url": url}
