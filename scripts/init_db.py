"""
Script pour initialiser la base de données avec un compte admin par défaut
"""
import asyncio
import sys
import os

# Ajouter le répertoire parent au path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.core.database import async_session_maker, init_db
from app.models.user import User, UserRole
from app.core.security import get_password_hash


async def create_admin_user():
    """Créer l'utilisateur admin à partir des variables d'environnement.

    SÉCURITÉ : aucun compte admin par défaut n'est créé. Le numéro et le mot
    de passe DOIVENT être fournis via `ADMIN_PHONE` et `ADMIN_PASSWORD`
    (ex: variables Railway). Sans ces variables, l'étape est ignorée — on
    ne crée jamais d'admin avec des identifiants devinables.
    """
    admin_phone = os.getenv("ADMIN_PHONE")
    admin_password = os.getenv("ADMIN_PASSWORD")

    if not admin_phone or not admin_password:
        print("ℹ️  ADMIN_PHONE / ADMIN_PASSWORD non définis — création admin ignorée")
        return

    if len(admin_password) < 12:
        print("⛔ ADMIN_PASSWORD trop court (min 12 caractères) — création admin annulée")
        return

    async with async_session_maker() as session:
        from sqlalchemy import select
        query = select(User).where(User.phone == admin_phone)
        result = await session.execute(query)
        existing_admin = result.scalar_one_or_none()

        if existing_admin:
            print("✅ Admin user already exists")
            return

        admin = User(
            phone=admin_phone,
            password_hash=get_password_hash(admin_password),
            role=UserRole.ADMIN,
            is_active=True,
            is_verified=True
        )

        session.add(admin)
        await session.commit()

        print(f"✅ Admin user created successfully (phone: {admin_phone})")


async def main():
    """Fonction principale"""
    print("🚀 Initializing database...")
    
    # Créer les tables
    await init_db()
    print("✅ Database tables created")
    
    # Créer l'admin
    await create_admin_user()
    
    print("\n✅ Database initialization complete!")


if __name__ == "__main__":
    asyncio.run(main())
