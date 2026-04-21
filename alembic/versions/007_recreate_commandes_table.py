"""Recréer la table commandes avec le schéma complet et correct

Revision ID: 007_recreate_commandes
Revises: 006_missing_core
Create Date: 2026-04-21

Supprime et recrée la table commandes avec le bon schéma complet.
Idempotent : gère les états partiels d'une exécution précédente échouée.
"""
from typing import Sequence, Union
from alembic import op

revision: str = "007_recreate_commandes"
down_revision: Union[str, None] = "006_missing_core"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nettoyer un éventuel état partiel d'une migration précédente échouée
    op.execute("DROP TABLE IF EXISTS commandes_old CASCADE")

    # S'assurer que les types ENUM existent
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'commandestatus') THEN
                CREATE TYPE commandestatus AS ENUM (
                    'CREEE','DIFFUSEE','ACCEPTEE','EN_RECUPERATION',
                    'EN_LIVRAISON','TERMINEE','ANNULEE'
                );
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'modepaiement') THEN
                CREATE TYPE modepaiement AS ENUM ('CASH','MOBILE_MONEY');
            END IF;
        END $$;
    """)

    # Supprimer la table existante (CASCADE pour les FK éventuelles)
    op.execute("DROP TABLE IF EXISTS commandes CASCADE")

    # Recréer avec le schéma complet et correct
    op.execute("""
        CREATE TABLE commandes (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            numero_commande VARCHAR(50) NOT NULL UNIQUE,
            partenaire_id UUID NOT NULL REFERENCES partenaires(id) ON DELETE CASCADE,
            livreur_id UUID REFERENCES livreurs(id) ON DELETE SET NULL,
            adresse_client VARCHAR(500),
            latitude_client FLOAT,
            longitude_client FLOAT,
            contact_client_nom VARCHAR(255) NOT NULL,
            contact_client_telephone VARCHAR(20) NOT NULL,
            instructions_speciales TEXT,
            description_colis TEXT,
            exige_code_livraison BOOLEAN NOT NULL DEFAULT FALSE,
            code_livraison VARCHAR(10),
            location_token VARCHAR(64) UNIQUE,
            location_shared_at TIMESTAMP,
            tracking_token VARCHAR(64) UNIQUE,
            mode_paiement modepaiement NOT NULL DEFAULT 'CASH',
            paiement_confirme VARCHAR(10) NOT NULL DEFAULT 'non',
            prix_propose FLOAT NOT NULL,
            commission_plateforme FLOAT NOT NULL,
            montant_livreur FLOAT NOT NULL,
            distance_km FLOAT,
            duree_estimee_minutes INTEGER,
            status commandestatus NOT NULL DEFAULT 'CREEE',
            note_livreur INTEGER,
            commentaire_livreur TEXT,
            diffusee_at TIMESTAMP,
            acceptee_at TIMESTAMP,
            recuperee_at TIMESTAMP,
            livree_at TIMESTAMP,
            annulee_at TIMESTAMP,
            raison_annulation TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)

    # Index
    op.execute("CREATE INDEX ix_commandes_numero_commande ON commandes (numero_commande)")
    op.execute("CREATE INDEX ix_commandes_location_token ON commandes (location_token)")
    op.execute("CREATE INDEX ix_commandes_tracking_token ON commandes (tracking_token)")


def downgrade() -> None:
    pass
