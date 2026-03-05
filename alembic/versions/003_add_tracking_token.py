"""Ajouter tracking_token pour le suivi client

Revision ID: 003_tracking
Revises: 002_add_location
Create Date: 2026-03-05
"""
from typing import Sequence, Union
from alembic import op

revision: str = '003_tracking'
down_revision: Union[str, None] = '002_add_location'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE commandes ADD COLUMN IF NOT EXISTS tracking_token VARCHAR(64)")
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_commandes_tracking_token'
            ) THEN
                ALTER TABLE commandes ADD CONSTRAINT uq_commandes_tracking_token UNIQUE (tracking_token);
            END IF;
        END $$;
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_commandes_tracking_token ON commandes (tracking_token)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_commandes_tracking_token")
    op.execute("ALTER TABLE commandes DROP CONSTRAINT IF EXISTS uq_commandes_tracking_token")
    op.execute("ALTER TABLE commandes DROP COLUMN IF EXISTS tracking_token")
