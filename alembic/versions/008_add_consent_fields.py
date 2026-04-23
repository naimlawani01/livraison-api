"""Ajouter les champs de consentement légal sur livreurs et partenaires

Revision ID: 008_consent_fields
Revises: 007_recreate_commandes
Create Date: 2026-04-23

Ajoute consent_accepted_at et consent_version sur les deux tables.
Ces champs permettent de tracer l'acceptation des CGU / Politique de confidentialité.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "008_consent_fields"
down_revision: Union[str, None] = "007_recreate_commandes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE livreurs
            ADD COLUMN IF NOT EXISTS consent_accepted_at TIMESTAMP WITHOUT TIME ZONE,
            ADD COLUMN IF NOT EXISTS consent_version VARCHAR(10)
    """)

    op.execute("""
        ALTER TABLE partenaires
            ADD COLUMN IF NOT EXISTS consent_accepted_at TIMESTAMP WITHOUT TIME ZONE,
            ADD COLUMN IF NOT EXISTS consent_version VARCHAR(10)
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE livreurs
            DROP COLUMN IF EXISTS consent_accepted_at,
            DROP COLUMN IF EXISTS consent_version
    """)

    op.execute("""
        ALTER TABLE partenaires
            DROP COLUMN IF EXISTS consent_accepted_at,
            DROP COLUMN IF EXISTS consent_version
    """)
