from sqlalchemy import Column, String, Float, ForeignKey, DateTime, Text, Index
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime, timezone
import uuid
from ..core.database import Base


class CreditTransaction(Base):
    """Journal du Crédit d'un expéditeur (côté expediteur).

    Miroir de WalletTransaction, mais pour le Crédit prépayé de l'expéditeur :
      - type 'recharge'   : entrée (PSP) — augmente le Crédit
      - type 'commission' : sortie      — débitée à chaque course (liée à commande_id)

    Pas de relation ORM (comme WalletTransaction) : uniquement des FK, pour
    garder la configuration des mappers simple et sans back_populates.
    """
    __tablename__ = "credit_transactions"

    # Accélère l'historique d'un expéditeur trié par date desc.
    __table_args__ = (
        Index("ix_credit_transactions_expediteur_created", "expediteur_id", "created_at"),
    )

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    expediteur_id = Column(UUID(as_uuid=True), ForeignKey("expediteurs.id", ondelete="CASCADE"), nullable=False)
    type          = Column(String(20), nullable=False)   # recharge | commission
    montant       = Column(Float, nullable=False)
    solde_avant   = Column(Float, nullable=False)
    solde_apres   = Column(Float, nullable=False)
    description   = Column(Text, nullable=True)
    commande_id   = Column(UUID(as_uuid=True), ForeignKey("commandes.id", ondelete="SET NULL"), nullable=True)
    statut        = Column(String(20), nullable=False, default="complete")  # complete | en_attente | refuse
    geniuspay_reference = Column(String(100), nullable=True)
    created_at    = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
