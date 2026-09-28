"""Accès à la base : pas de fuite d'informations, entrées typées, garde-fous prod."""
import inspect
import os
import subprocess
import sys

import pytest

from tests.test_webhook import _creer_expediteur, session  # noqa: F401


def test_debug_force_a_false_en_production():
    env = {**os.environ, "ENVIRONMENT": "production", "DEBUG": "True",
           "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/x", "SECRET_KEY": "x"}
    out = subprocess.run(
        [sys.executable, "-c", "from app.core.config import settings; print(settings.DEBUG)"],
        env=env, capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip().endswith("False")


@pytest.mark.parametrize("module, nom, param", [
    ("app.api.v1.endpoints.courses", "get_course_details", "course_id"),
    ("app.api.v1.endpoints.courses", "annuler_course", "course_id"),
    ("app.api.v1.endpoints.admin", "marquer_rembourse", "course_id"),
    ("app.api.v1.endpoints.admin", "rejeter_retrait", "txn_id"),
    ("app.api.v1.endpoints.tracking", "generate_tracking_link", "course_id"),
])
def test_identifiants_types_uuid(module, nom, param):
    """Un identifiant invalide est rejeté à l'entrée (422), jamais envoyé à la base."""
    import importlib
    from uuid import UUID
    fn = getattr(importlib.import_module(module), nom)
    assert inspect.signature(fn).parameters[param].annotation is UUID


async def test_erreur_psp_non_divulguee(session, monkeypatch):
    from fastapi import HTTPException
    import app.services.genius_pay_service as gps
    from app.api.v1.endpoints import credit as credit_mod
    from app.api.v1.endpoints.credit import RechargeCreditRequest, recharger_credit

    async def _panne(**kwargs):
        raise gps.GeniusPayError("GeniusPay erreur 500: {\"internal\": \"db-host=10.0.3.7 wallet=W-SECRET\"}")
    monkeypatch.setattr(gps, "initier_paiement", _panne)
    monkeypatch.setattr(credit_mod.settings, "GENIUSPAY_API_KEY", "k")
    _, p = await _creer_expediteur(session)
    with pytest.raises(HTTPException) as exc:
        await recharger_credit(RechargeCreditRequest(montant=50_000), p, session)
    assert exc.value.status_code == 502
    assert "10.0.3.7" not in exc.value.detail and "W-SECRET" not in exc.value.detail


def test_docker_compose_n_expose_rien_au_reseau():
    compose = open("docker-compose.yml", encoding="utf-8").read()
    for port in ("5432", "6379", "8000"):
        assert f'"127.0.0.1:{port}:{port}"' in compose
