"""Ajoute `courses.payeur` : qui règle la course (expediteur | client).

Revision ID: 021_add_payeur
Revises: 020_rename_course
Create Date: 2026-09-25

Règle métier (cf. app/services/pricing.py) : la commission est garantie par le
Crédit de l'expéditeur ; si c'est le client qui paie (Mobile Money obligatoire),
elle est rendue à l'expéditeur au paiement.

Backfill aligné sur le défaut appliqué aux apps antérieures : Mobile Money →
client, cash → expediteur. Idempotent (IF NOT EXISTS) car `init_db()` peut avoir
déjà créé la colonne via `create_all` sur une base neuve.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "021_add_payeur"
down_revision: Union[str, None] = "020_rename_course"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE courses ADD COLUMN IF NOT EXISTS payeur VARCHAR(20) "
        "NOT NULL DEFAULT 'expediteur'"
    )
    op.execute("UPDATE courses SET payeur = 'client' WHERE mode_paiement = 'MOBILE_MONEY'")


def downgrade() -> None:
    op.execute("ALTER TABLE courses DROP COLUMN IF EXISTS payeur")
