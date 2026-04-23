"""Créer la table wallet_transactions

Revision ID: 009_wallet_transactions
Revises: 008_consent_fields
Create Date: 2026-04-23
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "009_wallet_transactions"
down_revision: Union[str, None] = "008_consent_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS wallet_transactions (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            livreur_id  UUID NOT NULL REFERENCES livreurs(id) ON DELETE CASCADE,
            type        VARCHAR(20) NOT NULL,   -- 'credit' | 'retrait' | 'bonus'
            montant     FLOAT NOT NULL,
            solde_avant FLOAT NOT NULL,
            solde_apres FLOAT NOT NULL,
            description TEXT,
            commande_id UUID REFERENCES commandes(id) ON DELETE SET NULL,
            statut      VARCHAR(20) NOT NULL DEFAULT 'complete',  -- 'complete' | 'en_attente' | 'refuse'
            created_at  TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_wallet_transactions_livreur ON wallet_transactions(livreur_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_wallet_transactions_created ON wallet_transactions(created_at DESC)")

    # Ajouter solde_disponible si absent (idempotent)
    op.execute("""
        ALTER TABLE livreurs
            ADD COLUMN IF NOT EXISTS solde_disponible FLOAT NOT NULL DEFAULT 0.0
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS wallet_transactions")
