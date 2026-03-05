"""Ajouter location_token, location_shared_at et rendre adresse_client nullable

Revision ID: 002_add_location
Revises: 001_baseline
Create Date: 2026-03-05
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '002_add_location'
down_revision: Union[str, None] = '001_baseline'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE commandes ADD COLUMN IF NOT EXISTS location_token VARCHAR(64)")
    op.execute("ALTER TABLE commandes ADD COLUMN IF NOT EXISTS location_shared_at TIMESTAMP")
    op.execute("ALTER TABLE commandes ALTER COLUMN adresse_client DROP NOT NULL")
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_commandes_location_token'
            ) THEN
                ALTER TABLE commandes ADD CONSTRAINT uq_commandes_location_token UNIQUE (location_token);
            END IF;
        END $$;
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_commandes_location_token ON commandes (location_token);
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_commandes_location_token")
    op.execute("ALTER TABLE commandes DROP CONSTRAINT IF EXISTS uq_commandes_location_token")
    op.drop_column('commandes', 'location_shared_at')
    op.drop_column('commandes', 'location_token')
    op.alter_column('commandes', 'adresse_client', existing_type=sa.String(500), nullable=False)
