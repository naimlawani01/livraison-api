"""Crédit expéditeur : colonne credit_solde (partenaires) + table credit_transactions

Revision ID: 018_credit_expediteur
Revises: 017
Create Date: 2026-08-04

Nouveau modèle : l'expéditeur (partenaire) dispose d'un Crédit prépayé qui couvre
les commissions plateforme. Idempotent (IF NOT EXISTS) comme les migrations
précédentes.
"""
from typing import Sequence, Union
from alembic import op

revision: str = "018_credit_expediteur"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Solde de Crédit sur l'expéditeur (partenaire)
    op.execute("""
        ALTER TABLE partenaires
            ADD COLUMN IF NOT EXISTS credit_solde FLOAT NOT NULL DEFAULT 0.0
    """)

    # Journal des mouvements de Crédit
    op.execute("""
        CREATE TABLE IF NOT EXISTS credit_transactions (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            partenaire_id UUID NOT NULL REFERENCES partenaires(id) ON DELETE CASCADE,
            type          VARCHAR(20) NOT NULL,   -- 'recharge' | 'commission'
            montant       FLOAT NOT NULL,
            solde_avant   FLOAT NOT NULL,
            solde_apres   FLOAT NOT NULL,
            description   TEXT,
            commande_id   UUID REFERENCES commandes(id) ON DELETE SET NULL,
            statut        VARCHAR(20) NOT NULL DEFAULT 'complete',
            geniuspay_reference VARCHAR(100),
            created_at    TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_credit_transactions_partenaire_created
            ON credit_transactions(partenaire_id, created_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS credit_transactions")
    op.execute("ALTER TABLE partenaires DROP COLUMN IF EXISTS credit_solde")
