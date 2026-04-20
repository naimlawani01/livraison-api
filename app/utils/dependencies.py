from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from ..core.database import get_db
from ..core.security import decode_token
from ..models.user import User, UserRole
from ..models.partenaire import Partenaire
from ..models.livreur import Livreur

security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    """Récupérer l'utilisateur courant depuis le token JWT"""
    token = credentials.credentials
    payload = decode_token(token)
    
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide"
        )
    
    query = select(User).where(User.id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Utilisateur non trouvé"
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compte désactivé"
        )
    
    return user


async def get_current_partenaire(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Partenaire:
    """Récupérer le partenaire courant"""
    if current_user.role != UserRole.PARTENAIRE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès réservé aux partenaires"
        )
    
    query = select(Partenaire).where(Partenaire.user_id == current_user.id)
    result = await db.execute(query)
    partenaire = result.scalar_one_or_none()
    
    if not partenaire:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Partenaire non trouvé"
        )
    
    return partenaire


async def get_current_livreur(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Livreur:
    """Récupérer le livreur courant"""
    if current_user.role != UserRole.LIVREUR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès réservé aux livreurs"
        )
    
    query = select(Livreur).where(Livreur.user_id == current_user.id)
    result = await db.execute(query)
    livreur = result.scalar_one_or_none()
    
    if not livreur:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Livreur non trouvé"
        )
    
    return livreur


async def get_current_admin(
    current_user: User = Depends(get_current_user)
) -> User:
    """Vérifier que l'utilisateur est admin"""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès réservé aux administrateurs"
        )
    
    return current_user
