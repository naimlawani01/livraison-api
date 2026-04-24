from sqlalchemy import Column, String, Float, ForeignKey, Boolean, DateTime, Integer, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid
from ..core.database import Base


class Livreur(Base):
    """Modèle pour les livreurs (taxi-moto)"""
    __tablename__ = "livreurs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    
    # Informations personnelles
    nom_complet = Column(String(255), nullable=False)
    email = Column(String(255), nullable=True)
    
    # Documents (stocker les URLs/chemins)
    piece_identite_url = Column(String(500), nullable=True)
    permis_conduire_url = Column(String(500), nullable=True)
    photo_profil_url = Column(String(500), nullable=True)
    
    # Véhicule
    type_vehicule = Column(String(50), nullable=True)  # moto, scooter, etc.
    marque_modele = Column(String(100), nullable=True)
    plaque_immatriculation = Column(String(50), nullable=True)
    
    # Géolocalisation
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    derniere_position_maj = Column(DateTime, nullable=True)
    
    # Disponibilité
    is_disponible = Column(Boolean, default=False)
    is_en_course = Column(Boolean, default=False)
    
    # Validation
    is_verified = Column(Boolean, default=False)
    verified_at = Column(DateTime, nullable=True)
    
    # Statistiques
    note_moyenne = Column(Float, default=0.0)
    nombre_evaluations = Column(Integer, default=0)
    nombre_courses_completees = Column(Integer, default=0)
    nombre_courses_annulees = Column(Integer, default=0)
    
    # Paiements
    total_gains = Column(Float, default=0.0)
    solde_disponible = Column(Float, default=0.0)
    
    # Métadonnées
    device_token = Column(String(500), nullable=True)  # Pour notifications push
    preferences = Column(JSON, nullable=True)  # Préférences du livreur
    
    # Consentement légal
    consent_accepted_at = Column(DateTime, nullable=True)
    consent_version = Column(String(10), nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    
    # Relations
    user = relationship("User", backref="livreur", uselist=False)
    commandes = relationship("Commande", back_populates="livreur")
    
    def __repr__(self):
        return f"<Livreur {self.nom_complet}>"
