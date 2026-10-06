"""Échec de livraison et retour du colis.

Revision ID: 024_add_retour_colis
Revises: 023_add_ecart_livraison
Create Date: 2026-10-06

* Deux valeurs dans le type enum Postgres des statuts (`commandestatus`, nom figé
  lors du renommage commande → course) : RETOUR, RETOURNEE.
* Colonnes de suivi de l'échec et des frais de retour sur `courses`.

Idempotent (IF NOT EXISTS) : `init_db()` peut avoir créé colonnes et valeurs via
`create_all` sur une base neuve.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "024_add_retour_colis"
down_revision: Union[str, None] = "023_add_ecart_livraison"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ADD VALUE ne peut pas être utilisé dans la même transaction qu'il est créé :
    # bloc autocommit dédié.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE commandestatus ADD VALUE IF NOT EXISTS 'RETOUR'")
        op.execute("ALTER TYPE commandestatus ADD VALUE IF NOT EXISTS 'RETOURNEE'")

    op.execute("ALTER TABLE courses ADD COLUMN IF NOT EXISTS arrivee_client_at TIMESTAMP WITH TIME ZONE")
    op.execute("ALTER TABLE courses ADD COLUMN IF NOT EXISTS echec_livraison_raison VARCHAR(30)")
    op.execute("ALTER TABLE courses ADD COLUMN IF NOT EXISTS echec_livraison_at TIMESTAMP WITH TIME ZONE")
    op.execute("ALTER TABLE courses ADD COLUMN IF NOT EXISTS retournee_at TIMESTAMP WITH TIME ZONE")
    op.execute("ALTER TABLE courses ADD COLUMN IF NOT EXISTS frais_retour DOUBLE PRECISION")
    op.execute(
        "ALTER TABLE courses ADD COLUMN IF NOT EXISTS frais_retour_restant "
        "DOUBLE PRECISION NOT NULL DEFAULT 0"
    )
    # Recherche des frais de retour impayés d'un expéditeur (création de course,
    # recharge) : index partiel, quasi vide en régime normal.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_courses_frais_retour_dus "
        "ON courses (expediteur_id) WHERE frais_retour_restant > 0"
    )


def downgrade() -> None:
    # Les valeurs d'un enum Postgres ne se suppriment pas simplement : on ne
    # retire que les colonnes.
    op.execute("DROP INDEX IF EXISTS ix_courses_frais_retour_dus")
    for col in ("frais_retour_restant", "frais_retour", "retournee_at",
                "echec_livraison_at", "echec_livraison_raison", "arrivee_client_at"):
        op.execute(f"ALTER TABLE courses DROP COLUMN IF EXISTS {col}")
