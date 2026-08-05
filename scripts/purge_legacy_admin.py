"""
One-shot : neutralise l'ancien compte admin par défaut (`phone=00000000`,
mot de passe `admin123`) qui était créé automatiquement avant le correctif
de sécurité.

À lancer UNE FOIS contre la base de production :

    DATABASE_URL=<url_prod> python scripts/purge_legacy_admin.py

Le compte est supprimé s'il n'a aucune donnée liée ; sinon il est désactivé
(is_active=False) et son mot de passe est rendu inutilisable.
"""
import asyncio
import os
import sys
import secrets

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import select
from app.core.database import async_session_maker
from app.core.security import get_password_hash
from app.models.user import User, UserRole

LEGACY_ADMIN_PHONE = "00000000"


async def purge() -> None:
    async with async_session_maker() as session:
        result = await session.execute(
            select(User).where(User.phone == LEGACY_ADMIN_PHONE)
        )
        admin = result.scalar_one_or_none()

        if not admin:
            print("✅ Aucun compte legacy '00000000' — rien à faire")
            return

        if admin.role != UserRole.ADMIN:
            print("⚠️  Compte '00000000' trouvé mais ce n'est PAS un admin — abandon par sécurité")
            return

        # Neutralisation : désactivation + mot de passe aléatoire non communiqué.
        # (On ne supprime pas la ligne pour éviter de casser d'éventuelles FK.)
        admin.is_active = False
        admin.is_verified = False
        admin.password_hash = get_password_hash(secrets.token_urlsafe(32))
        await session.commit()
        print("✅ Compte legacy '00000000' désactivé et mot de passe invalidé")


if __name__ == "__main__":
    asyncio.run(purge())
