"""Tests du webhook GeniusPay — recharge du Crédit + idempotence.

On appelle directement `webhook_geniuspay(request, db)` avec une Request Starlette
construite à la main et la vérification de signature stubée (on teste la logique
métier, pas la crypto — déjà couverte ailleurs).
"""
import json
import os
import uuid

import pytest
import pytest_asyncio

TEST_URL = os.environ.get("DATABASE_TEST_URL", "sqlite+aiosqlite:////tmp/sonaiyaa_test_webhook.db")


@pytest_asyncio.fixture
async def session():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.core.database import Base
    import app.models  # noqa: F401

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
def _stub_signature(monkeypatch):
    """La signature HMAC est validée ailleurs — ici on la considère bonne."""
    import app.services.genius_pay_service as gps
    monkeypatch.setattr(gps, "verify_webhook_signature", lambda *a, **k: True)


async def _creer_expediteur(session, credit=0.0):
    from app.models.user import User, UserRole
    from app.models.expediteur import Expediteur
    user = User(
        id=uuid.uuid4(),
        phone=f"+224600{uuid.uuid4().int % 1000000:06d}",
        role=UserRole.EXPEDITEUR, is_verified=True,
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


def _make_request(payload):
    from starlette.requests import Request
    body = json.dumps(payload).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http", "method": "POST", "path": "/webhooks/geniuspay",
        "headers": [(b"x-webhook-signature", b"sig"), (b"x-webhook-timestamp", b"0")],
        "query_string": b"", "client": ("127.0.0.1", 12345),
    }
    return Request(scope, receive)


def _recharge_payload(expediteur_id, montant, reference):
    return {
        "event": "payment.success",
        "data": {
            "reference": reference,
            "amount": montant,
            "metadata": {
                "type": "credit_recharge",
                "expediteur_id": str(expediteur_id),
                "montant": montant,
            },
        },
    }


class TestWebhookRecharge:
    async def test_recharge_credite_le_credit(self, session):
        from app.api.v1.endpoints.payments import webhook_geniuspay
        from app.services import credit_service
        _, p = await _creer_expediteur(session, credit=0)
        pid = p.id
        res = await webhook_geniuspay(_make_request(_recharge_payload(pid, 50_000, "MTX-1")), session)
        assert res["ok"] is True
        assert await credit_service.credit_disponible(session, pid) == 50_000

    async def test_idempotence_meme_reference(self, session):
        from app.api.v1.endpoints.payments import webhook_geniuspay
        from app.services import credit_service
        _, p = await _creer_expediteur(session, credit=0)
        pid = p.id
        payload = _recharge_payload(pid, 50_000, "MTX-DUP")
        await webhook_geniuspay(_make_request(payload), session)
        await webhook_geniuspay(_make_request(payload), session)  # rejoue le même webhook
        # Appliqué UNE seule fois malgré le rejeu.
        assert await credit_service.credit_disponible(session, pid) == 50_000

    async def test_expediteur_id_invalide_ne_crash_pas(self, session):
        from app.api.v1.endpoints.payments import webhook_geniuspay
        payload = {
            "event": "payment.success",
            "data": {
                "reference": "MTX-X", "amount": 50_000,
                "metadata": {"type": "credit_recharge", "expediteur_id": "pas-un-uuid", "montant": 50_000},
            },
        }
        res = await webhook_geniuspay(_make_request(payload), session)
        assert res["ok"] is False


# ── Paiement client d'une course : Crédit rendu à l'expéditeur ────────────────

def _course_payload(course_id, reference):
    return {
        "event": "payment.success",
        "data": {"reference": reference, "metadata": {"course_id": str(course_id)}},
    }


class TestWebhookPaiementCourse:
    @pytest.fixture(autouse=True)
    def _stubs(self, monkeypatch):
        from app.services import sms_service as sms_mod
        from app.services.matching_service import MatchingService

        async def _noop(*a, **k):
            return 0
        monkeypatch.setattr(sms_mod.sms_service, "envoyer_sms_course", _noop)
        monkeypatch.setattr(MatchingService, "diffuser_course", _noop)

    async def _creer_course(self, session, payeur):
        from app.api.v1.endpoints.courses import create_course
        from app.models.course import ModePaiement
        from app.schemas.course import CourseCreate
        user, p = await _creer_expediteur(session, credit=50_000)
        payload = CourseCreate(
            contact_client_nom="Client", contact_client_telephone="620000000",
            prix_propose=1, mode_paiement=ModePaiement.MOBILE_MONEY, payeur=payeur,
            exige_code_livraison=False,
        )
        cmd = await create_course(payload, p, session)
        return user, p, cmd

    async def test_paiement_client_rend_la_commission(self, session):
        from app.api.v1.endpoints.payments import webhook_geniuspay
        from app.models.course import Payeur
        from app.services import credit_service
        _, p, cmd = await self._creer_course(session, Payeur.CLIENT)
        assert await credit_service.credit_disponible(session, p.id) == 48_800
        await webhook_geniuspay(_make_request(_course_payload(cmd.id, "PAY-1")), session)
        assert await credit_service.credit_disponible(session, p.id) == 50_000
        # rejouer le webhook ne rend rien de plus
        await webhook_geniuspay(_make_request(_course_payload(cmd.id, "PAY-1")), session)
        assert await credit_service.credit_disponible(session, p.id) == 50_000

    async def test_annulation_apres_paiement_client_ne_rembourse_pas_deux_fois(self, session):
        from app.api.v1.endpoints.courses import annuler_course
        from app.api.v1.endpoints.payments import webhook_geniuspay
        from app.models.course import Payeur
        from app.schemas.course import CourseAnnulation
        from app.services import credit_service
        user, p, cmd = await self._creer_course(session, Payeur.CLIENT)
        await webhook_geniuspay(_make_request(_course_payload(cmd.id, "PAY-2")), session)
        await annuler_course(cmd.id, CourseAnnulation(raison="test"), user, session)
        assert await credit_service.credit_disponible(session, p.id) == 50_000

    async def test_paiement_expediteur_garde_la_commission(self, session):
        from app.api.v1.endpoints.payments import webhook_geniuspay
        from app.models.course import Payeur
        from app.services import credit_service
        _, p, cmd = await self._creer_course(session, Payeur.EXPEDITEUR)
        await webhook_geniuspay(_make_request(_course_payload(cmd.id, "PAY-3")), session)
        assert await credit_service.credit_disponible(session, p.id) == 48_800

