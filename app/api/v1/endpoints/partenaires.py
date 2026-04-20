from fastapi import APIRouter, Depends, HTTPException, status
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
