from pydantic import BaseModel, Field, EmailStr
from typing import Optional, Dict
from datetime import datetime
from uuid import UUID


class LivreurBase(BaseModel):
    """Schéma de base pour un livreur"""
    nom_complet: str = Field(..., min_length=2, max_length=255)
    email: Optional[EmailStr] = None


class LivreurCreate(LivreurBase):
    """Schéma pour créer un livreur"""
    type_vehicule: Optional[str] = None
    marque_modele: Optional[str] = None
    plaque_immatriculation: Optional[str] = None


class LivreurUpdate(BaseModel):
    """Schéma pour mettre à jour un livreur"""
    nom_complet: Optional[str] = Field(None, min_length=2, max_length=255)
    email: Optional[EmailStr] = None
    type_vehicule: Optional[str] = None
    marque_modele: Optional[str] = None
    plaque_immatriculation: Optional[str] = None
    piece_identite_url: Optional[str] = None
    permis_conduire_url: Optional[str] = None
    photo_profil_url: Optional[str] = None


class LivreurLocationUpdate(BaseModel):
    """Mise à jour de la position du livreur"""
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class LivreurDisponibiliteUpdate(BaseModel):
    """Mise à jour de la disponibilité"""
    is_disponible: bool


class LivreurResponse(LivreurBase):
    """Réponse livreur"""
    id: UUID
    user_id: UUID
    type_vehicule: Optional[str]
    marque_modele: Optional[str]
    plaque_immatriculation: Optional[str]
    piece_identite_url: Optional[str] = None
    permis_conduire_url: Optional[str] = None
    photo_profil_url: Optional[str] = None
    latitude: Optional[float]
    longitude: Optional[float]
    derniere_position_maj: Optional[datetime]
    is_disponible: bool
    is_en_course: bool
    is_verified: bool
    note_moyenne: float
    nombre_evaluations: int
    nombre_courses_completees: int
    total_gains: float
    created_at: datetime
    
    class Config:
        from_attributes = True


class LivreurPublicResponse(BaseModel):
    """Réponse publique livreur (pour partenaires)"""
    id: UUID
    nom_complet: str
    note_moyenne: float
    nombre_evaluations: int
    type_vehicule: Optional[str]
    
    class Config:
        from_attributes = True
