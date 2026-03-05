from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from uuid import UUID
from ..models.commande import CommandeStatus, ModePaiement


class CommandeBase(BaseModel):
    """Schéma de base pour une commande"""
    adresse_client: Optional[str] = Field(None, max_length=500)
    contact_client_nom: str = Field(..., min_length=2, max_length=255)
    contact_client_telephone: str = Field(..., min_length=8, max_length=20)
    instructions_speciales: Optional[str] = None


class CommandeCreate(CommandeBase):
    """Schéma pour créer une commande"""
    latitude_client: Optional[float] = Field(None, ge=-90, le=90)
    longitude_client: Optional[float] = Field(None, ge=-180, le=180)
    prix_propose: float = Field(..., gt=0, description="Prix proposé pour la livraison")
    mode_paiement: ModePaiement = Field(default=ModePaiement.CASH, description="Mode de paiement")


class CommandeUpdate(BaseModel):
    """Mise à jour d'une commande"""
    status: CommandeStatus


class CommandeAnnulation(BaseModel):
    """Annulation d'une commande"""
    raison: str = Field(..., min_length=2, max_length=500, description="Raison de l'annulation")


class CommandeEvaluation(BaseModel):
    """Évaluation d'une commande"""
    note_livreur: int = Field(..., ge=1, le=5)
    commentaire_livreur: Optional[str] = Field(None, max_length=1000)


class CommandeResponse(CommandeBase):
    """Réponse commande"""
    id: UUID
    numero_commande: str
    restaurant_id: UUID
    livreur_id: Optional[UUID]
    latitude_client: Optional[float]
    longitude_client: Optional[float]
    prix_propose: float
    commission_plateforme: float
    montant_livreur: float
    mode_paiement: ModePaiement
    paiement_confirme: str
    distance_km: Optional[float]
    duree_estimee_minutes: Optional[int]
    status: CommandeStatus
    note_livreur: Optional[int]
    commentaire_livreur: Optional[str]
    location_token: Optional[str]
    location_shared_at: Optional[datetime]
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


class CommandeWithDetails(CommandeResponse):
    """Commande avec détails restaurant et livreur"""
    restaurant: Optional[dict] = None
    livreur: Optional[dict] = None


class RestaurantInfo(BaseModel):
    """Infos restaurant pour les courses disponibles"""
    id: UUID
    nom: str
    adresse: str
    latitude: float
    longitude: float
    
    class Config:
        from_attributes = True


class CommandeDisponibleResponse(BaseModel):
    """Commande disponible avec infos restaurant et distance depuis le livreur"""
    id: UUID
    numero_commande: str
    restaurant_id: UUID
    adresse_client: str
    latitude_client: Optional[float] = None
    longitude_client: Optional[float] = None
    contact_client_nom: str
    contact_client_telephone: str
    instructions_speciales: Optional[str] = None
    prix_propose: float
    commission_plateforme: float
    montant_livreur: float
    distance_km: Optional[float] = None
    duree_estimee_minutes: Optional[int] = None
    status: CommandeStatus
    created_at: datetime
    
    # Infos restaurant
    restaurant_nom: str
    restaurant_adresse: str
    restaurant_latitude: float
    restaurant_longitude: float
    
    # Distance livreur -> restaurant
    distance_livreur_km: Optional[float] = None
    duree_livreur_minutes: Optional[int] = None
    
    class Config:
        from_attributes = True
