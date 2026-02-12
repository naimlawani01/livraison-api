from pydantic import BaseModel, Field, validator
from typing import Optional
from datetime import datetime
from uuid import UUID
from ..models.user import UserRole


class UserBase(BaseModel):
    """Schéma de base pour un utilisateur"""
    phone: str = Field(..., min_length=8, max_length=20, description="Numéro de téléphone")


class UserCreate(UserBase):
    """Schéma pour créer un utilisateur"""
    role: UserRole
    password: Optional[str] = None


class UserLogin(BaseModel):
    """Schéma pour connexion"""
    phone: str
    password: Optional[str] = None


class OTPRequest(BaseModel):
    """Demande d'envoi OTP"""
    phone: str


class OTPVerify(BaseModel):
    """Vérification OTP"""
    phone: str
    otp_code: str


class UserResponse(UserBase):
    """Réponse utilisateur"""
    id: UUID
    role: UserRole
    is_active: bool
    is_verified: bool
    created_at: datetime
    last_login: Optional[datetime]
    
    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    """Réponse avec tokens"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse


class DeviceTokenUpdate(BaseModel):
    """Mise à jour du device token pour les notifications push"""
    device_token: str = Field(..., min_length=10, max_length=500)
