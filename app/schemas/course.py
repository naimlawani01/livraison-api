from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime
from uuid import UUID
from ..models.course import CourseStatus, ModePaiement
from ..utils.phone import normalize_guinea_phone, InvalidGuineaPhoneError


class CourseBase(BaseModel):
    """Schéma de base pour une course"""
    adresse_client: Optional[str] = Field(None, max_length=500)
    contact_client_nom: str = Field(..., min_length=2, max_length=255)
    contact_client_telephone: str = Field(..., description="Téléphone client guinéen")
    instructions_speciales: Optional[str] = None
    description_colis: Optional[str] = Field(
        None,
        max_length=2000,
        description="Nature ou description du colis / course (pour le livreur)",
    )

    @field_validator("contact_client_telephone", mode="before")
    @classmethod
    def _normalize_client_phone(cls, v):
        if v is None:
            return v
        try:
            return normalize_guinea_phone(str(v))
        except InvalidGuineaPhoneError as e:
            raise ValueError(str(e))


class CourseCreate(CourseBase):
    """Schéma pour créer une course"""
    latitude_client: Optional[float] = Field(None, ge=-90, le=90)
    longitude_client: Optional[float] = Field(None, ge=-180, le=180)
    prix_propose: float = Field(..., gt=0, description="Prix proposé pour la livraison")
    mode_paiement: ModePaiement = Field(default=ModePaiement.CASH, description="Mode de paiement")
    exige_code_livraison: Optional[bool] = Field(default=False, description="Exiger un code PIN à la livraison")
    nature_colis: str = Field(default="standard", description="standard | alimentaire | fragile | documents | volumineux")


class CourseUpdate(BaseModel):
    """Mise à jour d'une course"""
    status: CourseStatus


class CourseAnnulation(BaseModel):
    """Annulation d'une course"""
    raison: str = Field(..., min_length=2, max_length=500, description="Raison de l'annulation")


class CourseEvaluation(BaseModel):
    """Évaluation d'une course"""
    note_livreur: int = Field(..., ge=1, le=5)
    commentaire_livreur: Optional[str] = Field(None, max_length=1000)


class CourseResponse(CourseBase):
    """Réponse course"""
    id: UUID
    numero_course: str
    expediteur_id: UUID
    livreur_id: Optional[UUID]
    latitude_client: Optional[float]
    longitude_client: Optional[float]
    prix_propose: float
    commission_plateforme: float
    montant_livreur: float
    mode_paiement: ModePaiement
    paiement_confirme: str
    geniuspay_reference: Optional[str] = None
    exige_code_livraison: bool
    distance_km: Optional[float]
    duree_estimee_minutes: Optional[int]
    status: CourseStatus
    note_livreur: Optional[int]
    commentaire_livreur: Optional[str]
    location_token: Optional[str]
    location_shared_at: Optional[datetime]
    tracking_token: Optional[str]
    created_at: datetime
    updated_at: datetime
    diffusee_at: Optional[datetime]
    acceptee_at: Optional[datetime]
    recuperee_at: Optional[datetime]
    livree_at: Optional[datetime]
    annulee_at: Optional[datetime]
    raison_annulation: Optional[str]
    
    class Config:
        from_attributes = True


class CourseWithDetails(CourseResponse):
    """Course avec détails expediteur et livreur"""
    expediteur: Optional[dict] = None
    livreur: Optional[dict] = None
    code_livraison: Optional[str] = None


class ExpediteurInfo(BaseModel):
    """Infos expediteur pour les courses disponibles"""
    id: UUID
    nom: str
    adresse: str
    latitude: float
    longitude: float
    
    class Config:
        from_attributes = True


class CourseDisponibleResponse(BaseModel):
    """Course disponible avec infos expediteur et distance depuis le livreur"""
    id: UUID
    numero_course: str
    expediteur_id: UUID
    adresse_client: Optional[str] = None
    latitude_client: Optional[float] = None
    longitude_client: Optional[float] = None
    contact_client_nom: str
    contact_client_telephone: str
    instructions_speciales: Optional[str] = None
    description_colis: Optional[str] = None
    prix_propose: float
    commission_plateforme: float
    montant_livreur: float
    distance_km: Optional[float] = None
    duree_estimee_minutes: Optional[int] = None
    status: CourseStatus
    created_at: datetime
    mode_paiement: Optional[str] = "CASH"
    paiement_confirme: Optional[str] = "non"
    exige_code_livraison: bool
    
    # Infos expediteur
    expediteur_nom: str
    expediteur_adresse: str
    expediteur_latitude: float
    expediteur_longitude: float
    
    # Distance livreur -> expediteur
    distance_livreur_km: Optional[float] = None
    duree_livreur_minutes: Optional[int] = None
    
    class Config:
        from_attributes = True
