"""add geniuspay fields

Revision ID: 012_add_geniuspay_fields
Revises: 011_fix_datetime_timezone
Create Date: 2025-01-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "012_add_geniuspay_fields"
down_revision = "011_fix_datetime_timezone"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Champs GeniusPay sur commandes
    op.add_column(
        "commandes",
        sa.Column("geniuspay_reference", sa.String(100), nullable=True),
    )
    op.add_column(
        "commandes",
        sa.Column("geniuspay_checkout_url", sa.Text(), nullable=True),
    )

    # Référence GeniusPay sur wallet_transactions (pour les payouts livreur)
    op.add_column(
        "wallet_transactions",
        sa.Column("geniuspay_reference", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("commandes", "geniuspay_reference")
    op.drop_column("commandes", "geniuspay_checkout_url")
    op.drop_column("wallet_transactions", "geniuspay_reference")
