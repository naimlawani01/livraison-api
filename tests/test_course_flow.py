"""Tests d'intégration du flux de course — l'argent qui transite dans les endpoints.

On appelle directement les fonctions d'endpoint (create_course, update_course_status,
annuler_course) avec une DB de test (SQLite par défaut, ou DATABASE_TEST_URL). Les
appels externes (SMS PasseInfo) sont stubés ; sans position GPS, GeniusPay et la
diffusion ne se déclenchent pas.

But : prouver que le nouveau modèle est câblé au bon endroit —
  • création CASH  → débite la commission du Crédit (bloque si insuffisant)
  • création MoMo  → ne touche pas le Crédit
  • fin MoMo       → crédite les Gains du livreur
  • fin CASH       → ne crédite PAS le livreur (payé cash), mais compte total_gains
  • annulation CASH→ rembourse le Crédit
"""
import os
import uuid

import pytest
import pytest_asyncio

TEST_URL = os.environ.get("DATABASE_TEST_URL", "sqlite+aiosqlite:////tmp/sonaiyaa_test_course.db")


@pytest_asyncio.fixture
async def session():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.core.database import Base
    import app.models  # noqa: F401 — enregistre les tables

    engine = create_async_engine(TEST_URL, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture(autouse=True)
def _stub_sms(monkeypatch):
    """Neutralise l'envoi de SMS (PasseInfo) déclenché à chaque création de course."""
    import app.services.sms_service as sms_mod

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(sms_mod.sms_service, "envoyer_sms_course", _noop)


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
        latitude=9.5, longitude=-13.7, credit_solde=credit, is_verified=True,
    )
    session.add(p)
    await session.commit()
    return user, p


async def _creer_livreur(session):
    from app.models.user import User, UserRole
    from app.models.livreur import Livreur
    user = User(
        id=uuid.uuid4(),
        phone=f"+224601{uuid.uuid4().int % 1000000:06d}",
        role=UserRole.LIVREUR,
        is_verified=True,
    )
    session.add(user)
    await session.flush()
    liv = Livreur(
        id=uuid.uuid4(), user_id=user.id, nom_complet="Sow",
        is_verified=True, is_disponible=True, solde_disponible=0.0, total_gains=0.0,
    )
    session.add(liv)
    await session.commit()
    return user, liv


def _payload(mode):
    from app.schemas.course import CourseCreate
    return CourseCreate(
        contact_client_nom="Client Test",
        contact_client_telephone="622334455",
        prix_propose=10000,  # ignoré (le backend recalcule), mais requis > 0 par le schéma
        mode_paiement=mode,
        nature_colis="standard",
        exige_code_livraison=False,
    )


# ── Création ─────────────────────────────────────────────────────────────────

class TestCreation:
    async def test_cash_debite_la_commission_du_credit(self, session):
        from app.api.v1.endpoints.courses import create_course
        from app.models.course import ModePaiement, CourseStatus
        from app.services import credit_service
        _, p = await _creer_expediteur(session, credit=50_000)
        cmd = await create_course(_payload(ModePaiement.CASH), p, session)
        assert cmd.status == CourseStatus.CREEE
        assert cmd.commission_plateforme == 1_200      # 12 % de 10 000 (plancher)
        assert cmd.montant_livreur == 8_800
        assert await credit_service.credit_disponible(session, p.id) == 48_800

    async def test_credit_insuffisant_bloque_la_creation(self, session):
        from fastapi import HTTPException
        from sqlalchemy import select, func
        from app.api.v1.endpoints.courses import create_course
        from app.models.course import Course, ModePaiement
        from app.services import credit_service
        _, p = await _creer_expediteur(session, credit=500)   # < 1 200
        p_id = p.id  # capturé avant : le rollback interne expire l'objet p
        with pytest.raises(HTTPException) as exc:
            await create_course(_payload(ModePaiement.CASH), p, session)
        assert exc.value.status_code == 400
        # Rien créé, Crédit intact (rollback)
        assert await credit_service.credit_disponible(session, p_id) == 500
        n = (await session.execute(select(func.count()).select_from(Course))).scalar()
        assert n == 0

    async def test_momo_ne_touche_pas_le_credit(self, session):
        from app.api.v1.endpoints.courses import create_course
        from app.models.course import ModePaiement
        from app.services import credit_service
        _, p = await _creer_expediteur(session, credit=50_000)
        await create_course(_payload(ModePaiement.MOBILE_MONEY), p, session)
        assert await credit_service.credit_disponible(session, p.id) == 50_000


# ── Fin de course ────────────────────────────────────────────────────────────

class TestCompletion:
    async def _course_en_livraison(self, session, mode):
        from app.api.v1.endpoints.courses import create_course
        from app.models.course import CourseStatus
        _, p = await _creer_expediteur(session, credit=50_000)
        _, liv = await _creer_livreur(session)
        cmd = await create_course(_payload(mode), p, session)
        cmd.livreur_id = liv.id
        cmd.status = CourseStatus.EN_LIVRAISON
        await session.commit()
        return cmd, liv

    async def test_momo_credite_les_gains(self, session):
        from app.api.v1.endpoints.courses import update_course_status
        from app.models.course import ModePaiement, CourseStatus
        cmd, liv = await self._course_en_livraison(session, ModePaiement.MOBILE_MONEY)
        montant = cmd.montant_livreur
        await update_course_status(cmd.id, CourseStatus.TERMINEE, None, liv, session)
        await session.refresh(liv)
        assert liv.solde_disponible == montant   # Gains crédités
        assert liv.total_gains == montant

    async def test_cash_ne_credite_pas_le_livreur(self, session):
        from app.api.v1.endpoints.courses import update_course_status
        from app.models.course import ModePaiement, CourseStatus
        cmd, liv = await self._course_en_livraison(session, ModePaiement.CASH)
        montant = cmd.montant_livreur
        await update_course_status(cmd.id, CourseStatus.TERMINEE, None, liv, session)
        await session.refresh(liv)
        assert liv.solde_disponible == 0          # payé cash → pas de crédit Gains
        assert liv.total_gains == montant         # stat lifetime comptée


# ── Annulation ───────────────────────────────────────────────────────────────

class TestAnnulation:
    async def test_annulation_cash_rembourse_le_credit(self, session):
        from app.api.v1.endpoints.courses import create_course, annuler_course
        from app.models.course import ModePaiement, CourseStatus
        from app.schemas.course import CourseAnnulation
        from app.services import credit_service
        user_p, p = await _creer_expediteur(session, credit=50_000)
        cmd = await create_course(_payload(ModePaiement.CASH), p, session)
        assert await credit_service.credit_disponible(session, p.id) == 48_800
        await annuler_course(cmd.id, CourseAnnulation(raison="test"), user_p, session)
        await session.refresh(cmd)
        assert cmd.status == CourseStatus.ANNULEE
        assert await credit_service.credit_disponible(session, p.id) == 50_000  # remboursé
