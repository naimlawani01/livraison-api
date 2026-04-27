from sqlalchemy import Column, String, Float, ForeignKey, DateTime, Text, Enum as SQLEnum, Integer, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
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
    partenaire_id = Column(UUID(as_uuid=True), ForeignKey("partenaires.id", ondelete="CASCADE"), nullable=False)
    livreur_id = Column(UUID(as_uuid=True), ForeignKey("livreurs.id", ondelete="SET NULL"), nullable=True)
    
    # Informations de livraison
    adresse_client = Column(String(500), nullable=True)
    latitude_client = Column(Float, nullable=True)
    longitude_client = Column(Float, nullable=True)
    
    contact_client_nom = Column(String(255), nullable=False)
    contact_client_telephone = Column(String(20), nullable=False)
    
    # Instructions
    instructions_speciales = Column(Text, nullable=True)
    # Contenu à livrer (visible par le livreur)
    description_colis = Column(Text, nullable=True)
    
    # Sécurité de livraison
    exige_code_livraison = Column(Boolean, default=False, nullable=False)
    code_livraison = Column(String(10), nullable=True)
    
    # Lien de localisation client
    location_token = Column(String(64), unique=True, nullable=True, index=True)
    location_shared_at = Column(DateTime(timezone=True), nullable=True)
    
    # Lien de suivi client
    tracking_token = Column(String(64), unique=True, nullable=True, index=True)
    
    # Paiement
    mode_paiement = Column(SQLEnum(ModePaiement), default=ModePaiement.CASH, nullable=False)
    paiement_confirme = Column(String(10), default="non", nullable=False)  # non, oui
    geniuspay_reference = Column(String(100), nullable=True)
    geniuspay_checkout_url = Column(Text, nullable=True)
    
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
    diffusee_at = Column(DateTime(timezone=True), nullable=True)
    acceptee_at = Column(DateTime(timezone=True), nullable=True)
    recuperee_at = Column(DateTime(timezone=True), nullable=True)
    livree_at = Column(DateTime(timezone=True), nullable=True)
    annulee_at = Column(DateTime(timezone=True), nullable=True)
    raison_annulation = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    
    # Relations
    partenaire = relationship("Partenaire", back_populates="commandes")
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
