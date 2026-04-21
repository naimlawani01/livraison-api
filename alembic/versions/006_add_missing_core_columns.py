"""Ajouter les colonnes core manquantes sur commandes

Revision ID: 006_missing_core
Revises: 005_code_livraison
Create Date: 2026-04-21

Cette migration ajoute les colonnes fondamentales qui auraient pu manquer
si la table 'commandes' a été créée avec une version antérieure du schéma.
Toutes les opérations utilisent IF NOT EXISTS / DO $$ pour être idempotentes.
"""
from typing import Sequence, Union
from alembic import op

revision: str = "006_missing_core"
down_revision: Union[str, None] = "005_code_livraison"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Colonnes de relation (FK)
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='commandes' AND column_name='partenaire_id'
            ) THEN
                ALTER TABLE commandes ADD COLUMN partenaire_id UUID REFERENCES partenaires(id) ON DELETE CASCADE;
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='commandes' AND column_name='livreur_id'
            ) THEN
                ALTER TABLE commandes ADD COLUMN livreur_id UUID REFERENCES livreurs(id) ON DELETE SET NULL;
            END IF;
        END $$;
    """)

    # Colonnes contact client
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='commandes' AND column_name='contact_client_nom'
            ) THEN
                ALTER TABLE commandes ADD COLUMN contact_client_nom VARCHAR(255) NOT NULL DEFAULT '';
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='commandes' AND column_name='contact_client_telephone'
            ) THEN
                ALTER TABLE commandes ADD COLUMN contact_client_telephone VARCHAR(20) NOT NULL DEFAULT '';
            END IF;
        END $$;
    """)

    # Colonnes géolocalisation
    op.execute("ALTER TABLE commandes ADD COLUMN IF NOT EXISTS latitude_client FLOAT")
    op.execute("ALTER TABLE commandes ADD COLUMN IF NOT EXISTS longitude_client FLOAT")

    # Colonne instructions
    op.execute("ALTER TABLE commandes ADD COLUMN IF NOT EXISTS instructions_speciales TEXT")

    # Colonnes paiement
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='commandes' AND column_name='mode_paiement'
            ) THEN
                ALTER TABLE commandes ADD COLUMN mode_paiement modepaiement NOT NULL DEFAULT 'CASH';
            END IF;
        END $$;
    """)
    op.execute("ALTER TABLE commandes ADD COLUMN IF NOT EXISTS paiement_confirme VARCHAR(10) NOT NULL DEFAULT 'non'")

    # Colonnes tarification
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='commandes' AND column_name='prix_propose'
            ) THEN
                ALTER TABLE commandes ADD COLUMN prix_propose FLOAT NOT NULL DEFAULT 0;
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='commandes' AND column_name='commission_plateforme'
            ) THEN
                ALTER TABLE commandes ADD COLUMN commission_plateforme FLOAT NOT NULL DEFAULT 0;
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='commandes' AND column_name='montant_livreur'
            ) THEN
                ALTER TABLE commandes ADD COLUMN montant_livreur FLOAT NOT NULL DEFAULT 0;
            END IF;
        END $$;
    """)

    # Distance / durée
    op.execute("ALTER TABLE commandes ADD COLUMN IF NOT EXISTS distance_km FLOAT")
    op.execute("ALTER TABLE commandes ADD COLUMN IF NOT EXISTS duree_estimee_minutes INTEGER")

    # Statut
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='commandes' AND column_name='status'
            ) THEN
                ALTER TABLE commandes ADD COLUMN status commandestatus NOT NULL DEFAULT 'CREEE';
            END IF;
        END $$;
    """)

    # Évaluation
    op.execute("ALTER TABLE commandes ADD COLUMN IF NOT EXISTS note_livreur INTEGER")
    op.execute("ALTER TABLE commandes ADD COLUMN IF NOT EXISTS commentaire_livreur TEXT")

    # Horodatages événements
    op.execute("ALTER TABLE commandes ADD COLUMN IF NOT EXISTS diffusee_at TIMESTAMP")
    op.execute("ALTER TABLE commandes ADD COLUMN IF NOT EXISTS acceptee_at TIMESTAMP")
    op.execute("ALTER TABLE commandes ADD COLUMN IF NOT EXISTS recuperee_at TIMESTAMP")
    op.execute("ALTER TABLE commandes ADD COLUMN IF NOT EXISTS livree_at TIMESTAMP")
    op.execute("ALTER TABLE commandes ADD COLUMN IF NOT EXISTS annulee_at TIMESTAMP")
    op.execute("ALTER TABLE commandes ADD COLUMN IF NOT EXISTS raison_annulation TEXT")


def downgrade() -> None:
    # Pas de downgrade destructif pour les colonnes core
    pass
