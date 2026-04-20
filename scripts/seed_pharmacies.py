import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import select

from app.core.database import async_session_maker
from app.core.security import get_password_hash
from app.models.partenaire import Partenaire, TypePartenaire
from app.models.user import User, UserRole


async def seed_pharmacies():
    async with async_session_maker() as session:
        print("⏳ Création des pharmacies de test en cours...")

        phone1 = "0700000001"
        pwd1 = "pharma123"

        res = await session.execute(select(User).where(User.phone == phone1))
        if res.scalar_one_or_none():
            print("⚠️ Les pharmacies ont déjà été générées !")
            return

        user1 = User(
            phone=phone1,
            password_hash=get_password_hash(pwd1),
            role=UserRole.PARTENAIRE,
            is_verified=True,
        )
        session.add(user1)

        phone2 = "0700000002"
        pwd2 = "pharma123"
        user2 = User(
            phone=phone2,
            password_hash=get_password_hash(pwd2),
            role=UserRole.PARTENAIRE,
            is_verified=True,
        )
        session.add(user2)

        await session.commit()
        await session.refresh(user1)
        await session.refresh(user2)

        pharma1 = Partenaire(
            user_id=user1.id,
            type_partenaire=TypePartenaire.PHARMACIE,
            nom="Pharmacie Centrale Plus",
            description="Pharmacie de garde ouverte 24h/24. Dépôt d'ordonnance et produits grand public.",
            adresse="Avenue Principale, Centre-Ville",
            latitude=5.359951,
            longitude=-4.008256,
            is_open=True,
            is_verified=True,
            note_moyenne=4.8,
            nombre_evaluations=124,
        )
        session.add(pharma1)

        pharma2 = Partenaire(
            user_id=user2.id,
            type_partenaire=TypePartenaire.PHARMACIE,
            nom="Pharmacie de l'Espérance",
            description="Votre plateforme santé au quotidien.",
            adresse="Boulevard de la Lagune",
            latitude=5.3265,
            longitude=-4.0152,
            is_open=True,
            is_verified=True,
            note_moyenne=4.4,
            nombre_evaluations=58,
        )
        session.add(pharma2)

        await session.commit()

        print("✅ Données factices générées avec succès !")
        print("------------------------------------------")
        print(f"🏪 Pharmacie 1 : {pharma1.nom}")
        print(f"  Connexion   : {phone1}")
        print(f"  Mot de passe: {pwd1}")
        print("------------------------------------------")
        print(f"🏪 Pharmacie 2 : {pharma2.nom}")
        print(f"  Connexion   : {phone2}")
        print(f"  Mot de passe: {pwd2}")


if __name__ == "__main__":
    asyncio.run(seed_pharmacies())
