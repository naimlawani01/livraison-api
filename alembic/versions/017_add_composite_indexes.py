"""add composite indexes for hot query paths

Revision ID: 017
Revises: 016
Create Date: 2026-05-14

Why
----
Identified by backend audit (May 2026) — the most frequent queries filter
on multiple columns at once but no composite index exists. At 10k+ rows,
PostgreSQL ends up doing full table scans on:

  - "list mes commandes en cours" : commandes WHERE partenaire_id AND status
  - "courses dispo livreur"        : commandes WHERE livreur_id AND status
  - "admin dashboard du jour"      : commandes WHERE status AND created_at
  - "historique wallet livreur"    : wallet_transactions WHERE livreur_id ORDER BY created_at

Each composite index speeds up these queries by 10-100x as the table grows.

Run is non-blocking (CREATE INDEX CONCURRENTLY would be ideal but Alembic
inside a transaction prevents it — at our current table size (<5k rows)
the regular CREATE INDEX completes in <1s and is safe.
"""
from alembic import op


revision = '017'
down_revision = '016'
branch_labels = None
depends_on = None


def upgrade():
    # commandes : filtres fréquents côté partenaire et livreur
    op.create_index(
        'ix_commandes_partenaire_status',
        'commandes',
        ['partenaire_id', 'status'],
    )
    op.create_index(
        'ix_commandes_livreur_status',
        'commandes',
        ['livreur_id', 'status'],
    )
    # admin dashboard : "commandes par statut, triées par date"
    op.create_index(
        'ix_commandes_status_created',
        'commandes',
        ['status', 'created_at'],
    )

    # wallet_transactions : historique livreur trié par date desc
    op.create_index(
        'ix_wallet_transactions_livreur_created',
        'wallet_transactions',
        ['livreur_id', 'created_at'],
    )


def downgrade():
    op.drop_index('ix_wallet_transactions_livreur_created', table_name='wallet_transactions')
    op.drop_index('ix_commandes_status_created', table_name='commandes')
    op.drop_index('ix_commandes_livreur_status', table_name='commandes')
    op.drop_index('ix_commandes_partenaire_status', table_name='commandes')
