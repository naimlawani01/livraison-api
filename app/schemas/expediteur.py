from pydantic import BaseModel, Field, EmailStr
from typing import Optional, Dict
from datetime import datetime
from uuid import UUID
from ..models.expediteur import TypeExpediteur


class ExpediteurBase(BaseModel):
    """Schéma de base pour un expediteur"""
    type_expediteur: Optional[TypeExpediteur] = Field(default=TypeExpediteur.AUTRE)
    nom: str = Field(..., min_length=2, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    adresse: str = Field(..., min_length=5, max_length=500)
    email: Optional[EmailStr] = None
    telephone_secondaire: Optional[str] = None


class ExpediteurCreate(ExpediteurBase):
    """Schéma pour créer un expediteur"""

    type_expediteur: TypeExpediteur = Field(
        ...,
        description="Type d'activité (restaurant, pharmacie, etc.)",
    )
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    horaires: Optional[Dict] = None


class ExpediteurUpdate(BaseModel):
    """Schéma pour mettre à jour un expediteur"""
    type_expediteur: Optional[TypeExpediteur] = None
    nom: Optional[str] = Field(None, min_length=2, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    adresse: Optional[str] = Field(None, min_length=5, max_length=500)
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    email: Optional[EmailStr] = None
    telephone_secondaire: Optional[str] = None
    horaires: Optional[Dict] = None
    is_open: Optional[bool] = None


class ExpediteurResponse(ExpediteurBase):
    """Réponse expediteur"""
    id: UUID
    user_id: UUID
    latitude: float
    longitude: float
    horaires: Optional[Dict]
    is_open: bool
    is_verified: bool
    note_moyenne: float
    nombre_evaluations: int
    devanture_url: Optional[str] = None
    rccm_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
