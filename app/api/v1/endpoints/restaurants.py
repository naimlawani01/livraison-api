from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from ....core.database import get_db
from ....models.restaurant import Restaurant
from ....models.user import User
from ....schemas.restaurant import (
    RestaurantCreate,
    RestaurantUpdate,
    RestaurantResponse
)
from ....utils.dependencies import get_current_user, get_current_restaurant

router = APIRouter()


@router.post("/", response_model=RestaurantResponse, status_code=status.HTTP_201_CREATED)
async def create_restaurant(
    restaurant_data: RestaurantCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Créer le profil restaurant (après inscription)"""
    # Vérifier qu'il n'a pas déjà un profil
    query = select(Restaurant).where(Restaurant.user_id == current_user.id)
    result = await db.execute(query)
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Profil restaurant déjà existant"
        )
    
    # Créer le restaurant
    restaurant = Restaurant(
        user_id=current_user.id,
        nom=restaurant_data.nom,
        description=restaurant_data.description,
        adresse=restaurant_data.adresse,
        latitude=restaurant_data.latitude,
        longitude=restaurant_data.longitude,
        email=restaurant_data.email,
        telephone_secondaire=restaurant_data.telephone_secondaire,
        horaires=restaurant_data.horaires
    )
    
    db.add(restaurant)
    await db.commit()
    await db.refresh(restaurant)
    
    return restaurant


@router.get("/me", response_model=RestaurantResponse)
async def get_my_restaurant(
    restaurant: Restaurant = Depends(get_current_restaurant)
):
    """Obtenir mon profil restaurant"""
    return restaurant


@router.patch("/me", response_model=RestaurantResponse)
async def update_my_restaurant(
    restaurant_data: RestaurantUpdate,
    restaurant: Restaurant = Depends(get_current_restaurant),
    db: AsyncSession = Depends(get_db)
):
    """Mettre à jour mon profil restaurant"""
    update_dict = restaurant_data.model_dump(exclude_unset=True)
    
    for key, value in update_dict.items():
        setattr(restaurant, key, value)
    
    await db.commit()
    await db.refresh(restaurant)
    
    return restaurant


@router.get("/", response_model=List[RestaurantResponse])
async def list_restaurants(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db)
):
    """Lister les restaurants (pour admin ou public)"""
    query = select(Restaurant).offset(skip).limit(limit)
    result = await db.execute(query)
    restaurants = result.scalars().all()
    
    return restaurants


@router.get("/{restaurant_id}", response_model=RestaurantResponse)
async def get_restaurant(
    restaurant_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Obtenir un restaurant par ID"""
    query = select(Restaurant).where(Restaurant.id == restaurant_id)
    result = await db.execute(query)
    restaurant = result.scalar_one_or_none()
    
    if not restaurant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Restaurant non trouvé"
        )
    
    return restaurant
