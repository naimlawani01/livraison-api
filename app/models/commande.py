from sqlalchemy import Column, String, Float, ForeignKey, DateTime, Text, Enum as SQLEnum, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
import enum
from ..core.database import Base


class CommandeStatus(str, enum.Enum):
    """Statuts possibles d'une commande"""
    CREEE = "CREEE"
    DIFFUSEE = "DIFFUSEE"
    ACCEPTEE = "ACCEPTEE"
    EN_RECUPERATION = "EN_RECUPERATION"
    EN_LIVRAISON = "EN_LIVRAISON"
    TERMINEE = "TERMINEE"
    ANNULEE = "ANNULEE"


class ModePaiement(str, enum.Enum):
    """Modes de paiement disponibles"""
    CASH = "CASH"
    MOBILE_MONEY = "MOBILE_MONEY"


class Commande(Base):
    """Modèle pour les commandes de livraison"""
    __tablename__ = "commandes"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    numero_commande = Column(String(50), unique=True, nullable=False, index=True)
    
    # Relations
    restaurant_id = Column(UUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False)
    livreur_id = Column(UUID(as_uuid=True), ForeignKey("livreurs.id", ondelete="SET NULL"), nullable=True)
    
    # Informations de livraison
    adresse_client = Column(String(500), nullable=False)
    latitude_client = Column(Float, nullable=True)
    longitude_client = Column(Float, nullable=True)
    
    contact_client_nom = Column(String(255), nullable=False)
    contact_client_telephone = Column(String(20), nullable=False)
    
    # Instructions
    instructions_speciales = Column(Text, nullable=True)
    
    # Paiement
    mode_paiement = Column(SQLEnum(ModePaiement), default=ModePaiement.CASH, nullable=False)
    paiement_confirme = Column(String(10), default="non", nullable=False)  # non, oui
    
    # Tarification
    prix_propose = Column(Float, nullable=False)
    commission_plateforme = Column(Float, nullable=False)
    montant_livreur = Column(Float, nullable=False)
    
    # Distance
    distance_km = Column(Float, nullable=True)
    duree_estimee_minutes = Column(Integer, nullable=True)
    
    # Statut
    status = Column(SQLEnum(CommandeStatus), default=CommandeStatus.CREEE, nullable=False)
    
    # Évaluation
    note_livreur = Column(Integer, nullable=True)  # 1 à 5
    commentaire_livreur = Column(Text, nullable=True)
    
    # Historique des événements
    diffusee_at = Column(DateTime, nullable=True)
    acceptee_at = Column(DateTime, nullable=True)
    recuperee_at = Column(DateTime, nullable=True)
    livree_at = Column(DateTime, nullable=True)
    annulee_at = Column(DateTime, nullable=True)
    raison_annulation = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relations
    restaurant = relationship("Restaurant", back_populates="commandes")
    livreur = relationship("Livreur", back_populates="commandes")
    
    def __repr__(self):
        return f"<Commande {self.numero_commande} - {self.status}>"
    
    @staticmethod
    def generer_numero_commande() -> str:
        """Générer un numéro de commande unique"""
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        random_part = str(uuid.uuid4().hex[:6]).upper()
        return f"CMD-{timestamp}-{random_part}"
