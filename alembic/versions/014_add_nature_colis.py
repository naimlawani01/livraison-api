"""Ajoute la colonne `nature_colis` sur les commandes.

Permet de recalculer le prix après le partage GPS du client
(le prix dépend de la distance × multiplicateur du type de colis).

Revision ID: 014_add_nature_colis
Revises: 013_normalize_guinea_phones
Create Date: 2026-04-30 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "014_add_nature_colis"
down_revision = "013_normalize_guinea_phones"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "commandes",
        sa.Column(
            "nature_colis",
            sa.String(50),
            nullable=False,
            server_default="standard",
        ),
    )


def downgrade() -> None:
    op.drop_column("commandes", "nature_colis")
