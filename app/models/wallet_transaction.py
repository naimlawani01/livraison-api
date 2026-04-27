from sqlalchemy import Column, String, Float, ForeignKey, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime, timezone
import uuid
from ..core.database import Base


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    livreur_id  = Column(UUID(as_uuid=True), ForeignKey("livreurs.id", ondelete="CASCADE"), nullable=False)
    type        = Column(String(20), nullable=False)   # credit | retrait | bonus
    montant     = Column(Float, nullable=False)
    solde_avant = Column(Float, nullable=False)
    solde_apres = Column(Float, nullable=False)
    description = Column(Text, nullable=True)
    commande_id = Column(UUID(as_uuid=True), ForeignKey("commandes.id", ondelete="SET NULL"), nullable=True)
    statut      = Column(String(20), nullable=False, default="complete")  # complete | en_attente | en_cours | refuse
    geniuspay_reference = Column(String(100), nullable=True)
    created_at  = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
