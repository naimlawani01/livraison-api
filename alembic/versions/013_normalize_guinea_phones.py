"""Normalise tous les numéros de téléphone au format guinéen +224XXXXXXXXX.

Tables concernées :
  - users.phone
  - commandes.contact_client_telephone

Stratégie :
  • Si déjà au format `+224XXXXXXXXX` valide → on ne touche pas.
  • Si commence par `224`, `00224` ou rien et donne 9 chiffres locaux
    commençant par `6` → on préfixe `+224`.
  • Les autres numéros (anciens formats invalides, étrangers, etc.) sont
    laissés tels quels — ils déclencheront une erreur de validation à la
    prochaine connexion. C'est volontaire : on ne perd pas de données mais
    on force la migration UX côté utilisateur.

Revision ID: 013_normalize_guinea_phones
Revises: 012_add_geniuspay_fields
Create Date: 2026-04-29 00:00:00.000000
"""
from alembic import op


revision = "013_normalize_guinea_phones"
down_revision = "012_add_geniuspay_fields"
branch_labels = None
depends_on = None


# Postgres: regexp_replace pour ne garder que les chiffres,
# puis on traite les cas 00224 / 224 / brut et on vérifie que ça commence par 6.
_NORMALIZE_SQL = """
UPDATE {table} AS t SET {column} = '+224' || sub.local
FROM (
  SELECT
    {pk} AS id,
    CASE
      WHEN regexp_replace({column}, '\\D', '', 'g') LIKE '00224%'
        THEN substring(regexp_replace({column}, '\\D', '', 'g') FROM 6)
      WHEN regexp_replace({column}, '\\D', '', 'g') LIKE '224%'
        THEN substring(regexp_replace({column}, '\\D', '', 'g') FROM 4)
      ELSE regexp_replace({column}, '\\D', '', 'g')
    END AS local
  FROM {table}
  WHERE {column} IS NOT NULL AND {column} NOT LIKE '+224%'
) AS sub
WHERE t.{pk} = sub.id
  AND sub.local ~ '^6[0-9]{{8}}$';
"""


def upgrade() -> None:
    op.execute(_NORMALIZE_SQL.format(table="users", column="phone", pk="id"))
    op.execute(_NORMALIZE_SQL.format(
        table="commandes", column="contact_client_telephone", pk="id"
    ))


def downgrade() -> None:
    # Pas de downgrade : la normalisation est volontairement irréversible
    # car on ne sait pas reconstituer le format d'origine de chaque numéro.
    pass
