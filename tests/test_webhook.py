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
    from app.models.partenaire import Partenaire
    user = User(
        id=uuid.uuid4(),
        phone=f"+224600{uuid.uuid4().int % 1000000:06d}",
        role=UserRole.PARTENAIRE, is_verified=True,
    )
    session.add(user)
    await session.flush()
    p = Partenaire(
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


def _recharge_payload(partenaire_id, montant, reference):
    return {
        "event": "payment.success",
        "data": {
            "reference": reference,
            "amount": montant,
            "metadata": {
                "type": "credit_recharge",
                "partenaire_id": str(partenaire_id),
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

    async def test_partenaire_id_invalide_ne_crash_pas(self, session):
        from app.api.v1.endpoints.payments import webhook_geniuspay
        payload = {
            "event": "payment.success",
            "data": {
                "reference": "MTX-X", "amount": 50_000,
                "metadata": {"type": "credit_recharge", "partenaire_id": "pas-un-uuid", "montant": 50_000},
            },
        }
        res = await webhook_geniuspay(_make_request(payload), session)
        assert res["ok"] is False
