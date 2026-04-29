from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime
from uuid import UUID
from ..models.user import UserRole
from ..utils.phone import normalize_guinea_phone, InvalidGuineaPhoneError


def _validate_phone(value: str) -> str:
    """Validator partagé : normalise tout en `+224XXXXXXXXX`."""
    try:
        return normalize_guinea_phone(value)
    except InvalidGuineaPhoneError as e:
        raise ValueError(str(e))


class UserBase(BaseModel):
    """Schéma de base pour un utilisateur (lecture)."""
    phone: str = Field(..., description="Numéro guinéen au format +224XXXXXXXXX")


class UserCreate(BaseModel):
    """Schéma pour créer un utilisateur"""
    phone: str = Field(..., description="Numéro guinéen")
    role: UserRole
    password: Optional[str] = None

    _normalize_phone = field_validator("phone")(lambda cls, v: _validate_phone(v))


class UserLogin(BaseModel):
    """Schéma pour connexion"""
    phone: str
    password: Optional[str] = None

    _normalize_phone = field_validator("phone")(lambda cls, v: _validate_phone(v))


class OTPRequest(BaseModel):
    """Demande d'envoi OTP"""
    phone: str

    _normalize_phone = field_validator("phone")(lambda cls, v: _validate_phone(v))


class OTPVerify(BaseModel):
    """Vérification OTP"""
    phone: str
    otp_code: str

    _normalize_phone = field_validator("phone")(lambda cls, v: _validate_phone(v))


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
