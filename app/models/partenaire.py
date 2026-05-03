from sqlalchemy import Column, String, Float, Integer, ForeignKey, Boolean, JSON, DateTime, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid
import enum
from ..core.database import Base

class TypePartenaire(str, enum.Enum):
    RESTAURANT = "RESTAURANT"
    PHARMACIE = "PHARMACIE"
    SUPERMARCHE = "SUPERMARCHE"
    B2B = "B2B"
    AUTRE = "AUTRE"


class Partenaire(Base):
    """Modèle pour les partenaires/commerçants"""
    __tablename__ = "partenaires"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    
    # Informations du partenaire
    type_partenaire = Column(SQLEnum(TypePartenaire), default=TypePartenaire.AUTRE, nullable=False)
    nom = Column(String(255), nullable=False)
    description = Column(String(1000), nullable=True)
    adresse = Column(String(500), nullable=False)
    
    # Géolocalisation
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    
    # Contact
    email = Column(String(255), nullable=True)
    telephone_secondaire = Column(String(20), nullable=True)
    
    # Horaires (format JSON)
    horaires = Column(JSON, nullable=True)
    
    # Document de vérification
    devanture_url = Column(String(500), nullable=True)  # photo de la devanture du commerce

    # Statut
    is_open = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    
    # Notation
    note_moyenne = Column(Float, default=0.0)
    nombre_evaluations = Column(Integer, default=0)
    
    # Consentement légal
    consent_accepted_at = Column(DateTime, nullable=True)
    consent_version = Column(String(10), nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    
    # Relations
    user = relationship("User", backref="partenaire", uselist=False)
    commandes = relationship("Commande", back_populates="partenaire", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Partenaire {self.nom} ({self.type_partenaire})>"
