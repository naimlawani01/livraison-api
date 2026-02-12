from sqlalchemy import Column, String, Float, Integer, ForeignKey, Boolean, JSON, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
from ..core.database import Base


class Restaurant(Base):
    """Modèle pour les restaurants/commerçants"""
    __tablename__ = "restaurants"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    
    # Informations du restaurant
    nom = Column(String(255), nullable=False)
    description = Column(String(1000), nullable=True)
    adresse = Column(String(500), nullable=False)
    
    # Géolocalisation
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    
    # Contact
    email = Column(String(255), nullable=True)
    telephone_secondaire = Column(String(20), nullable=True)
    
    # Horaires (format JSON: {"lundi": {"ouvert": true, "debut": "08:00", "fin": "22:00"}})
    horaires = Column(JSON, nullable=True)
    
    # Statut
    is_open = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    
    # Notation
    note_moyenne = Column(Float, default=0.0)
    nombre_evaluations = Column(Integer, default=0)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relations
    user = relationship("User", backref="restaurant", uselist=False)
    commandes = relationship("Commande", back_populates="restaurant", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Restaurant {self.nom}>"
