from pydantic import BaseModel, Field, EmailStr
from typing import Optional, Dict
from datetime import datetime
from uuid import UUID
from ..models.partenaire import TypePartenaire


class PartenaireBase(BaseModel):
    """Schéma de base pour un partenaire"""
    type_partenaire: Optional[TypePartenaire] = Field(default=TypePartenaire.AUTRE)
    nom: str = Field(..., min_length=2, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    adresse: str = Field(..., min_length=5, max_length=500)
    email: Optional[EmailStr] = None
    telephone_secondaire: Optional[str] = None


class PartenaireCreate(PartenaireBase):
    """Schéma pour créer un partenaire"""

    type_partenaire: TypePartenaire = Field(
        ...,
        description="Type d'activité (restaurant, pharmacie, etc.)",
    )
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    horaires: Optional[Dict] = None


class PartenaireUpdate(BaseModel):
    """Schéma pour mettre à jour un partenaire"""
    type_partenaire: Optional[TypePartenaire] = None
    nom: Optional[str] = Field(None, min_length=2, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    adresse: Optional[str] = Field(None, min_length=5, max_length=500)
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    email: Optional[EmailStr] = None
    telephone_secondaire: Optional[str] = None
    horaires: Optional[Dict] = None
    is_open: Optional[bool] = None


class PartenaireResponse(PartenaireBase):
    """Réponse partenaire"""
    id: UUID
    user_id: UUID
    latitude: float
    longitude: float
    horaires: Optional[Dict]
    is_open: bool
    is_verified: bool
    note_moyenne: float
    nombre_evaluations: int
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True
