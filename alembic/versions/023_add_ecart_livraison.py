"""Ajoute `courses.ecart_livraison_km` (anti-fraude à la livraison).

Revision ID: 023_add_ecart_livraison
Revises: 022_add_remboursement
Create Date: 2026-09-28

Distance entre la position du livreur au moment de marquer la course livrée et
l'adresse déclarée du client. Idempotent (IF NOT EXISTS) car `init_db()` peut
avoir créé la colonne via `create_all` sur une base neuve.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "023_add_ecart_livraison"
down_revision: Union[str, None] = "022_add_remboursement"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE courses ADD COLUMN IF NOT EXISTS ecart_livraison_km DOUBLE PRECISION")


def downgrade() -> None:
    op.execute("ALTER TABLE courses DROP COLUMN IF EXISTS ecart_livraison_km")
