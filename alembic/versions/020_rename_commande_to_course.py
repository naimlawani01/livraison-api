"""Renommage commande → course : table, colonnes FK, index.

Revision ID: 020_rename_course
Revises: 019_rename_expediteur
Create Date: 2026-08-06

Aligne la couche DB sur le renommage produit/code `commande → course`.

Le TYPE enum Postgres reste nommé `commandestatus` (figé côté modèle via
`name="commandestatus"`) → invisible, aucun risque de rename de type. On ne
renomme ici que la TABLE, les COLONNES FK (`commande_id`) et les INDEX.

⚠️ Même précaution qu'en 019 : `init_db()` fait un `create_all` AVANT
`alembic upgrade head` → une table `courses` VIDE peut être pré-créée. On la
supprime alors, puis on renomme `commandes` (les vraies données) à sa place.
Idempotent (guards IF EXISTS).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "020_rename_course"
down_revision: Union[str, None] = "019_rename_expediteur"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. create_all a pu pré-créer une table `courses` VIDE alors que
    #    `commandes` existe encore avec les données → on jette la vide.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='courses')
               AND EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='commandes') THEN
                DROP TABLE courses CASCADE;
            END IF;
        END $$;
        """
    )

    # 2. Renommer la table commandes → courses.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='commandes')
               AND NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='courses') THEN
                ALTER TABLE commandes RENAME TO courses;
            END IF;
        END $$;
        """
    )

    # 3. Colonnes FK commande_id → course_id (les contraintes FK suivent le rename).
    for tbl in ("credit_transactions", "wallet_transactions"):
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='{tbl}' AND column_name='commande_id') THEN
                    ALTER TABLE {tbl} RENAME COLUMN commande_id TO course_id;
                END IF;
            END $$;
            """
        )

    # 4. Index composites.
    op.execute("ALTER INDEX IF EXISTS ix_commandes_expediteur_status RENAME TO ix_courses_expediteur_status")
    op.execute("ALTER INDEX IF EXISTS ix_commandes_livreur_status RENAME TO ix_courses_livreur_status")
    op.execute("ALTER INDEX IF EXISTS ix_commandes_status_created RENAME TO ix_courses_status_created")


def downgrade() -> None:
    op.execute("ALTER INDEX IF EXISTS ix_courses_expediteur_status RENAME TO ix_commandes_expediteur_status")
    op.execute("ALTER INDEX IF EXISTS ix_courses_livreur_status RENAME TO ix_commandes_livreur_status")
    op.execute("ALTER INDEX IF EXISTS ix_courses_status_created RENAME TO ix_commandes_status_created")
    for tbl in ("credit_transactions", "wallet_transactions"):
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='{tbl}' AND column_name='course_id') THEN
                    ALTER TABLE {tbl} RENAME COLUMN course_id TO commande_id;
                END IF;
            END $$;
            """
        )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='courses')
               AND NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='commandes') THEN
                ALTER TABLE courses RENAME TO commandes;
            END IF;
        END $$;
        """
    )
