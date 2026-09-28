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


# ── IP client non falsifiable (X-Forwarded-For) ───────────────────────────────

def _req_xff(xff, client="10.1.2.3"):
    from starlette.requests import Request

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers,
                    "query_string": b"", "client": (client, 1234)}, receive)


@pytest.mark.parametrize("xff, attendu", [
    ("41.223.50.7", "41.223.50.7"),                           # client réel, 1 proxy
    ("1.2.3.4, 41.223.50.7", "41.223.50.7"),                  # IP injectée à gauche ignorée
    ("127.0.0.1, 41.223.50.7", "41.223.50.7"),                # se faire passer pour localhost : ignoré
    ("9.9.9.9, 41.223.50.7, 10.0.0.5", "41.223.50.7"),        # saut interne Railway ignoré
    ("  bidon , 41.223.50.7", "41.223.50.7"),                 # valeur invalide ignorée
])
def test_ip_client_prend_l_ip_ajoutee_par_le_proxy(xff, attendu):
    from app.core.client_ip import ip_client
    assert ip_client(_req_xff(xff)) == attendu


def test_rotation_de_x_forwarded_for_ne_contourne_pas_le_rate_limit():
    """L'attaquant change la partie gauche à chaque requête : la clé ne bouge pas."""
    from app.core.rate_limit import _key_func
    cles = {_key_func(_req_xff(f"5.5.5.{i}, 41.223.50.7")) for i in range(20)}
    assert cles == {"41.223.50.7"}


def test_health_public_ne_revele_rien():
    import asyncio
    from app.main import health_check
    assert asyncio.run(health_check()) == {"status": "healthy"}
