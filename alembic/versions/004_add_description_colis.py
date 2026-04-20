"""Ajouter description_colis sur commandes

Revision ID: 004_desc_colis
Revises: 003_tracking
Create Date: 2026-04-13
"""
from typing import Sequence, Union

from alembic import op

revision: str = "004_desc_colis"
down_revision: Union[str, None] = "003_tracking"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE commandes ADD COLUMN IF NOT EXISTS description_colis TEXT"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE commandes DROP COLUMN IF EXISTS description_colis")
