"""Ajoute `courses.remboursement_du` / `courses.rembourse_at`.

Revision ID: 022_add_remboursement
Revises: 021_add_payeur
Create Date: 2026-09-28

Trace les paiements Mobile Money de clients à rembourser (course payée puis
annulée), traités à la main par l'admin. Idempotent (IF NOT EXISTS) car
`init_db()` peut avoir créé les colonnes via `create_all` sur une base neuve.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "022_add_remboursement"
down_revision: Union[str, None] = "021_add_payeur"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE courses ADD COLUMN IF NOT EXISTS remboursement_du DOUBLE PRECISION")
    op.execute("ALTER TABLE courses ADD COLUMN IF NOT EXISTS rembourse_at TIMESTAMP WITH TIME ZONE")


def downgrade() -> None:
    op.execute("ALTER TABLE courses DROP COLUMN IF EXISTS rembourse_at")
    op.execute("ALTER TABLE courses DROP COLUMN IF EXISTS remboursement_du")
