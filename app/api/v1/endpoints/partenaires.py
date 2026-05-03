from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from ....core.database import get_db
from ....models.partenaire import Partenaire
from ....models.user import User
from ....schemas.partenaire import (
    PartenaireCreate,
    PartenaireUpdate,
    PartenaireResponse
)
from ....utils.dependencies import get_current_user, get_current_partenaire
from ....services.storage_service import storage_service

router = APIRouter()


@router.post("/", response_model=PartenaireResponse, status_code=status.HTTP_201_CREATED)
async def create_partenaire(
    partenaire_data: PartenaireCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Créer le profil partenaire (après inscription)"""
    # Vérifier qu'il n'a pas déjà un profil
    query = select(Partenaire).where(Partenaire.user_id == current_user.id)
    result = await db.execute(query)
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Profil partenaire déjà existant"
        )
    
    # Créer le partenaire
    partenaire = Partenaire(
        user_id=current_user.id,
        type_partenaire=partenaire_data.type_partenaire,
        nom=partenaire_data.nom,
        description=partenaire_data.description,
        adresse=partenaire_data.adresse,
        latitude=partenaire_data.latitude,
        longitude=partenaire_data.longitude,
        email=partenaire_data.email,
        telephone_secondaire=partenaire_data.telephone_secondaire,
        horaires=partenaire_data.horaires
    )
    
    db.add(partenaire)
    await db.commit()
    await db.refresh(partenaire)
    
    return partenaire


@router.get("/me", response_model=PartenaireResponse)
async def get_my_partenaire(
    partenaire: Partenaire = Depends(get_current_partenaire)
):
    """Obtenir mon profil partenaire"""
    return partenaire


@router.patch("/me", response_model=PartenaireResponse)
async def update_my_partenaire(
    partenaire_data: PartenaireUpdate,
    partenaire: Partenaire = Depends(get_current_partenaire),
    db: AsyncSession = Depends(get_db)
):
    """Mettre à jour mon profil partenaire"""
    update_dict = partenaire_data.model_dump(exclude_unset=True)
    
    for key, value in update_dict.items():
        setattr(partenaire, key, value)
    
    await db.commit()
    await db.refresh(partenaire)
    
    return partenaire


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_account(
    partenaire: Partenaire = Depends(get_current_partenaire),
    db: AsyncSession = Depends(get_db)
):
    """Supprimer mon compte (droit à l’effacement RGPD)"""
    # Anonymiser les données personnelles plutôt que supprimer
    # pour conserver l’historique des commandes
    partenaire.nom = "Compte supprimé"
    partenaire.email = None
    partenaire.description = None
    partenaire.adresse = "Anonymisé"
    partenaire.latitude = 0.0
    partenaire.longitude = 0.0
    partenaire.telephone_secondaire = None
    partenaire.is_open = False

    # Anonymiser le user
    user_query = select(User).where(User.id == partenaire.user_id)
    result = await db.execute(user_query)
    user = result.scalar_one_or_none()
    if user:
        user.phone = f"deleted_{partenaire.id}"
        user.password_hash = "deleted"

    await db.commit()


@router.get("/", response_model=List[PartenaireResponse])
async def list_partenaires(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db)
):
    """Lister les partenaires (pour admin ou public)"""
    query = select(Partenaire).offset(skip).limit(limit)
    result = await db.execute(query)
    partenaires = result.scalars().all()
    
    return partenaires


@router.get("/{partenaire_id}", response_model=PartenaireResponse)
async def get_partenaire(
    partenaire_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Obtenir un partenaire par ID"""
    query = select(Partenaire).where(Partenaire.id == partenaire_id)
    result = await db.execute(query)
    partenaire = result.scalar_one_or_none()

    if not partenaire:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Partenaire non trouvé"
        )

    return partenaire


@router.post("/me/upload-document")
async def upload_devanture(
    file: UploadFile = File(...),
    partenaire: Partenaire = Depends(get_current_partenaire),
    db: AsyncSession = Depends(get_db)
):
    """Uploader la photo de la devanture du commerce vers Cloudflare R2."""
    content = await file.read()
    url = await storage_service.upload_document(
        file_data=content,
        folder="partenaires",
        original_filename=file.filename or "devanture.jpg",
        content_type=file.content_type or "image/jpeg",
    )
    partenaire.devanture_url = url
    await db.commit()
    return {"message": "Document uploadé avec succès", "url": url}

