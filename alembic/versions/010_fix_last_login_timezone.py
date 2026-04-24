"""Fix last_login column to use TIMESTAMP WITH TIME ZONE

Revision ID: 010_fix_last_login_timezone
Revises: 009_wallet_transactions
Create Date: 2026-04-24
"""
from typing import Sequence, Union
from alembic import op

revision: str = "010_fix_last_login_timezone"
down_revision: Union[str, None] = "009_wallet_transactions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE users
            ALTER COLUMN last_login TYPE TIMESTAMP WITH TIME ZONE
            USING last_login AT TIME ZONE 'UTC'
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE users
            ALTER COLUMN last_login TYPE TIMESTAMP WITHOUT TIME ZONE
            USING last_login AT TIME ZONE 'UTC'
    """)
