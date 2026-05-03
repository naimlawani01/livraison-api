"""add vehicule_doc to livreurs and devanture_url to partenaires

Revision ID: 015
Revises: 014_add_nature_colis
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa

revision = '015'
down_revision = '014_add_nature_colis'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('livreurs', sa.Column('vehicule_doc_url', sa.String(500), nullable=True))
    op.add_column('livreurs', sa.Column('vehicule_doc_type', sa.String(20), nullable=True))
    op.add_column('partenaires', sa.Column('devanture_url', sa.String(500), nullable=True))


def downgrade():
    op.drop_column('partenaires', 'devanture_url')
    op.drop_column('livreurs', 'vehicule_doc_type')
    op.drop_column('livreurs', 'vehicule_doc_url')
