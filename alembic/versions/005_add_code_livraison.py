"""Ajouter exige_code_livraison et code_livraison sur commandes

Revision ID: 005_code_livraison
Revises: 004_desc_colis
Create Date: 2026-04-14
"""
from typing import Sequence, Union

from alembic import op

revision: str = "005_code_livraison"
down_revision: Union[str, None] = "004_desc_colis"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE commandes ADD COLUMN IF NOT EXISTS exige_code_livraison BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE commandes ADD COLUMN IF NOT EXISTS code_livraison VARCHAR(10)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE commandes DROP COLUMN IF EXISTS code_livraison")
    op.execute("ALTER TABLE commandes DROP COLUMN IF EXISTS exige_code_livraison")
