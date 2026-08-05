"""Tests d'intégration DB du service Crédit.

⚠️ Ne s'exécutent QUE si ``DATABASE_TEST_URL`` est défini et pointe vers une base
de TEST (jamais la prod). Sans cette variable, tout le module est ignoré — impossible
de toucher la prod par accident.

    DATABASE_TEST_URL=postgresql+asyncpg://user:pass@localhost:5433/livraison_test \\
        .venv/bin/python -m pytest tests/test_credit_service.py -v
"""
import os
import uuid

import pytest
import pytest_asyncio

# Par défaut : SQLite (aiosqlite) — sûr, aucun setup, ne peut JAMAIS être la prod.
# Pour tester contre un vrai Postgres (verrous FOR UPDATE réels), définir
# DATABASE_TEST_URL vers une base de TEST dédiée.
TEST_URL = os.environ.get("DATABASE_TEST_URL", "sqlite+aiosqlite:////tmp/sonaiyaa_test_credit.db")


@pytest_asyncio.fixture
async def session():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.core.database import Base
    import app.models  # noqa: F401 — enregistre toutes les tables dans Base.metadata

    engine = create_async_engine(TEST_URL, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _creer_expediteur(session, credit=0.0):
    from app.models.user import User, UserRole
    from app.models.expediteur import Expediteur

    user = User(
        id=uuid.uuid4(),
        phone=f"+224600{uuid.uuid4().int % 1000000:06d}",
        role=UserRole.EXPEDITEUR,
        is_verified=True,
    )
    session.add(user)
    await session.flush()
    p = Expediteur(
        id=uuid.uuid4(), user_id=user.id, nom="Test", adresse="Conakry",
        latitude=9.5, longitude=-13.7, credit_solde=credit,
    )
    session.add(p)
    await session.commit()
    return p


class TestCreditService:
    async def test_recharge_augmente_le_solde(self, session):
        from app.services import credit_service
        p = await _creer_expediteur(session, credit=0.0)
        await credit_service.recharger(session, p.id, 50_000)
        assert await credit_service.credit_disponible(session, p.id) == 50_000

    async def test_debit_commission_diminue_le_solde(self, session):
        from app.services import credit_service
        p = await _creer_expediteur(session, credit=50_000)
        await credit_service.debiter_commission(session, p.id, 1_200)
        assert await credit_service.credit_disponible(session, p.id) == 48_800

    async def test_debit_refuse_si_credit_insuffisant(self, session):
        from app.services import credit_service
        from app.services.soldes import SoldeInsuffisant
        p = await _creer_expediteur(session, credit=1_000)
        with pytest.raises(SoldeInsuffisant):
            await credit_service.debiter_commission(session, p.id, 1_200)
        # le solde n'a pas bougé
        assert await credit_service.credit_disponible(session, p.id) == 1_000

    async def test_couvre_commission_garde_fou(self, session):
        from app.services import credit_service
        p = await _creer_expediteur(session, credit=1_200)
        assert await credit_service.couvre_commission(session, p.id, 1_200) is True
        assert await credit_service.couvre_commission(session, p.id, 1_201) is False


class TestRemboursement:
    async def test_remboursement_recredite(self, session):
        from app.services import credit_service
        p = await _creer_expediteur(session, credit=50_000)
        await credit_service.debiter_commission(session, p.id, 1_200)
        await credit_service.rembourser_commission(session, p.id, 1_200)
        assert await credit_service.credit_disponible(session, p.id) == 50_000


class TestAjustementGPS:
    async def test_delta_positif_debite_le_supplement(self, session):
        from app.services import credit_service
        p = await _creer_expediteur(session, credit=50_000)
        # commission recalculée de 1 200 → 1 560 : débiter 360
        await credit_service.ajuster_commission(session, p.id, 1_200, 1_560)
        assert await credit_service.credit_disponible(session, p.id) == 49_640

    async def test_delta_negatif_rembourse(self, session):
        from app.services import credit_service
        p = await _creer_expediteur(session, credit=50_000)
        # commission recalculée de 2 000 → 1 200 : rembourser 800
        await credit_service.ajuster_commission(session, p.id, 2_000, 1_200)
        assert await credit_service.credit_disponible(session, p.id) == 50_800

    async def test_jamais_sous_zero_best_effort(self, session):
        from app.services import credit_service
        p = await _creer_expediteur(session, credit=100)
        # delta +5 000 mais seulement 100 dispo → cap à 0, jamais négatif
        await credit_service.ajuster_commission(session, p.id, 0, 5_000)
        assert await credit_service.credit_disponible(session, p.id) == 0


class TestCycleComplet:
    async def test_recharge_debit_annulation(self, session):
        from app.services import credit_service
        p = await _creer_expediteur(session, credit=0)
        await credit_service.recharger(session, p.id, 50_000)
        await credit_service.debiter_commission(session, p.id, 1_680)  # course
        assert await credit_service.credit_disponible(session, p.id) == 48_320
        await credit_service.rembourser_commission(session, p.id, 1_680)  # annulée
        assert await credit_service.credit_disponible(session, p.id) == 50_000

    async def test_journal_enregistre_chaque_mouvement(self, session):
        from sqlalchemy import select, func
        from app.services import credit_service
        from app.models.credit_transaction import CreditTransaction
        p = await _creer_expediteur(session, credit=50_000)
        await credit_service.recharger(session, p.id, 10_000)
        await credit_service.debiter_commission(session, p.id, 1_200)
        n = (await session.execute(
            select(func.count()).where(CreditTransaction.expediteur_id == p.id)
        )).scalar()
        assert n == 2  # 1 recharge + 1 commission


class TestCreditAdmin:
    async def test_credit_manuel_sans_plancher(self, session):
        from app.services import credit_service
        p = await _creer_expediteur(session, credit=0)
        # 1 500 < plancher recharge (5 000), mais l'admin ajuste librement
        await credit_service.crediter_admin(session, p.id, 1_500, motif="correction")
        assert await credit_service.credit_disponible(session, p.id) == 1_500

    async def test_credit_manuel_montant_invalide(self, session):
        from app.services import credit_service
        from app.services.soldes import MontantInvalide
        p = await _creer_expediteur(session, credit=0)
        with pytest.raises(MontantInvalide):
            await credit_service.crediter_admin(session, p.id, 0)
