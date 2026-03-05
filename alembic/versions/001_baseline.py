"""Baseline: schema initial existant

Revision ID: 001_baseline
Revises: 
Create Date: 2026-03-05

Cette migration est un "stamp" — elle ne fait rien car la base existe déjà.
Elle sert de point de départ pour les futures migrations.
"""
from typing import Sequence, Union
from alembic import op

revision: str = '001_baseline'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # La base de données existe déjà avec toutes les tables.
    # Cette migration sert uniquement de point de départ.
    pass


def downgrade() -> None:
    pass
