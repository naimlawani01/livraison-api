"""Renommage partenaire → expediteur : table, colonnes FK, index.

Revision ID: 019_rename_expediteur
Revises: 018_credit_expediteur
Create Date: 2026-08-05

Aligne la couche DB sur le renommage produit/code `partenaire → expediteur`.

⚠️ Ordre de démarrage (start.sh) : `init_db()` fait un `create_all` AVANT
`alembic upgrade head`. Comme le modèle s'appelle désormais `expediteurs`,
create_all crée une table `expediteurs` VIDE juste avant cette migration. On la
supprime alors, puis on renomme `partenaires` (qui porte les vraies données) à sa
place. Tout est idempotent (guards IF EXISTS) → rejouable sans risque.

Note enum : le type Postgres de `type_partenaire` garde son nom d'origine (on ne
renomme que la COLONNE, pas le type). Sans impact au runtime (SQLAlchemy échange
des chaînes) ; un éventuel type orphelin créé par create_all est inoffensif.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "019_rename_expediteur"
down_revision: Union[str, None] = "018_credit_expediteur"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. create_all a pu pré-créer une table `expediteurs` VIDE alors que
    #    `partenaires` existe encore avec les données → on jette la vide.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='expediteurs')
               AND EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='partenaires') THEN
                DROP TABLE expediteurs CASCADE;
            END IF;
        END $$;
        """
    )

    # 2. Renommer la table partenaires → expediteurs.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='partenaires')
               AND NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='expediteurs') THEN
                ALTER TABLE partenaires RENAME TO expediteurs;
            END IF;
        END $$;
        """
    )

    # 3. Colonne enum type_partenaire → type_expediteur (le TYPE PG garde son nom).
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='expediteurs' AND column_name='type_partenaire') THEN
                ALTER TABLE expediteurs RENAME COLUMN type_partenaire TO type_expediteur;
            END IF;
        END $$;
        """
    )

    # 4. Colonnes FK partenaire_id → expediteur_id (les contraintes FK suivent
    #    automatiquement le renommage de table/colonne côté Postgres).
    for tbl in ("commandes", "credit_transactions"):
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='{tbl}' AND column_name='partenaire_id') THEN
                    ALTER TABLE {tbl} RENAME COLUMN partenaire_id TO expediteur_id;
                END IF;
            END $$;
            """
        )

    # 5. Index composites.
    op.execute("ALTER INDEX IF EXISTS ix_commandes_partenaire_status RENAME TO ix_commandes_expediteur_status")
    op.execute(
        "ALTER INDEX IF EXISTS ix_credit_transactions_partenaire_created "
        "RENAME TO ix_credit_transactions_expediteur_created"
    )


def downgrade() -> None:
    op.execute("ALTER INDEX IF EXISTS ix_commandes_expediteur_status RENAME TO ix_commandes_partenaire_status")
    op.execute(
        "ALTER INDEX IF EXISTS ix_credit_transactions_expediteur_created "
        "RENAME TO ix_credit_transactions_partenaire_created"
    )
    for tbl in ("commandes", "credit_transactions"):
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='{tbl}' AND column_name='expediteur_id') THEN
                    ALTER TABLE {tbl} RENAME COLUMN expediteur_id TO partenaire_id;
                END IF;
            END $$;
            """
        )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='expediteurs' AND column_name='type_expediteur') THEN
                ALTER TABLE expediteurs RENAME COLUMN type_expediteur TO type_partenaire;
            END IF;
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='expediteurs')
               AND NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='partenaires') THEN
                ALTER TABLE expediteurs RENAME TO partenaires;
            END IF;
        END $$;
        """
    )
