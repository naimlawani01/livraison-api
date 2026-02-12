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
    """Créer un utilisateur admin par défaut"""
    async with async_session_maker() as session:
        # Vérifier si admin existe déjà
        from sqlalchemy import select
        query = select(User).where(User.phone == "00000000")
        result = await session.execute(query)
        existing_admin = result.scalar_one_or_none()
        
        if existing_admin:
            print("✅ Admin user already exists")
            return
        
        # Créer l'admin
        admin = User(
            phone="00000000",  # Numéro fictif pour admin
            password_hash=get_password_hash("admin123"),
            role=UserRole.ADMIN,
            is_active=True,
            is_verified=True
        )
        
        session.add(admin)
        await session.commit()
        
        print("✅ Admin user created successfully")
        print("   Phone: 00000000")
        print("   Password: admin123")
        print("   ⚠️  CHANGE THE PASSWORD IN PRODUCTION!")


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
