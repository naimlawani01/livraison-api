"""Fix all DateTime columns to use TIMESTAMP WITH TIME ZONE

Revision ID: 011_fix_datetime_timezone
Revises: 010_fix_last_login_timezone
Create Date: 2026-04-24
"""
from typing import Sequence, Union
from alembic import op

revision: str = "011_fix_datetime_timezone"
down_revision: Union[str, None] = "010_fix_last_login_timezone"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # users
    op.execute("""
        ALTER TABLE users
            ALTER COLUMN otp_expires_at TYPE TIMESTAMP WITH TIME ZONE
            USING otp_expires_at AT TIME ZONE 'UTC'
    """)

    # livreurs
    op.execute("""
        ALTER TABLE livreurs
            ALTER COLUMN verified_at TYPE TIMESTAMP WITH TIME ZONE
            USING verified_at AT TIME ZONE 'UTC'
    """)
    op.execute("""
        ALTER TABLE livreurs
            ALTER COLUMN derniere_position_maj TYPE TIMESTAMP WITH TIME ZONE
            USING derniere_position_maj AT TIME ZONE 'UTC'
    """)

    # commandes
    for col in ["location_shared_at", "diffusee_at", "acceptee_at",
                "recuperee_at", "livree_at", "annulee_at"]:
        op.execute(f"""
            ALTER TABLE commandes
                ALTER COLUMN {col} TYPE TIMESTAMP WITH TIME ZONE
                USING {col} AT TIME ZONE 'UTC'
        """)


def downgrade() -> None:
    for col in ["location_shared_at", "diffusee_at", "acceptee_at",
                "recuperee_at", "livree_at", "annulee_at"]:
        op.execute(f"""
            ALTER TABLE commandes
                ALTER COLUMN {col} TYPE TIMESTAMP WITHOUT TIME ZONE
                USING {col} AT TIME ZONE 'UTC'
        """)
    op.execute("""
        ALTER TABLE livreurs
            ALTER COLUMN derniere_position_maj TYPE TIMESTAMP WITHOUT TIME ZONE
            USING derniere_position_maj AT TIME ZONE 'UTC'
    """)
    op.execute("""
        ALTER TABLE livreurs
            ALTER COLUMN verified_at TYPE TIMESTAMP WITHOUT TIME ZONE
            USING verified_at AT TIME ZONE 'UTC'
    """)
    op.execute("""
        ALTER TABLE users
            ALTER COLUMN otp_expires_at TYPE TIMESTAMP WITHOUT TIME ZONE
            USING otp_expires_at AT TIME ZONE 'UTC'
    """)
