"""add rccm_url to partenaires

Revision ID: 016
Revises: 015
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa

revision = '016'
down_revision = '015'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('partenaires', sa.Column('rccm_url', sa.String(500), nullable=True))


def downgrade():
    op.drop_column('partenaires', 'rccm_url')
