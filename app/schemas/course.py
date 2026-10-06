from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime
from uuid import UUID
from ..models.course import CourseStatus, ModePaiement, Payeur
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
    payeur: Optional[Payeur] = Field(
        default=None,
        description="Qui règle la course : expediteur | client (client ⇒ Mobile Money). "
                    "Par défaut : client si Mobile Money, expediteur si cash.",
    )
    # Activé par défaut : sans code, un livreur peut marquer « livré » sans avoir
    # remis le colis. Le code est envoyé au client par SMS.
    exige_code_livraison: Optional[bool] = Field(default=True, description="Exiger un code PIN à la livraison")
    nature_colis: str = Field(default="standard", description="standard | alimentaire | fragile | documents | volumineux")


class CourseUpdate(BaseModel):
    """Mise à jour d'une course"""
    status: CourseStatus


class CourseAnnulation(BaseModel):
    """Annulation d'une course"""
    raison: str = Field(..., min_length=2, max_length=500, description="Raison de l'annulation")


class EchecLivraison(BaseModel):
    """Livraison impossible déclarée par le livreur."""
    raison: str = Field(..., pattern="^(client_absent|refus_client)$",
                        description="client_absent ou refus_client")


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
    payeur: str = Payeur.EXPEDITEUR.value
    montant_a_encaisser: float
    paiement_confirme: str
    geniuspay_reference: Optional[str] = None
    geniuspay_checkout_url: Optional[str] = None
    remboursement_du: Optional[float] = None
    rembourse_at: Optional[datetime] = None
    ecart_livraison_km: Optional[float] = None
    arrivee_client_at: Optional[datetime] = None
    echec_livraison_raison: Optional[str] = None
    echec_livraison_at: Optional[datetime] = None
    retournee_at: Optional[datetime] = None
    frais_retour: Optional[float] = None
    frais_retour_restant: float = 0.0
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
    payeur: str = Payeur.EXPEDITEUR.value
    montant_a_encaisser: float
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
