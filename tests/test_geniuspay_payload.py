"""Régression du payload GeniusPay — la devise DOIT être GNF.

Bug historique : les montants du produit sont en GNF mais le payload n'envoyait
pas de devise (défaut XOF côté GeniusPay) → surfacturation ~15× (10 000 GNF
traités comme 10 000 XOF). Ces tests capturent le JSON envoyé sans réseau réel
et vérifient `currency == "GNF"` pour le checkout ET le payout.
"""
import pytest

import app.services.genius_pay_service as gps


class _FakeResp:
    status_code = 201
    url = "http://test/geniuspay"
    text = '{"data": {}}'

    def json(self):
        return {
            "data": {
                "reference": "MTX-1",
                "checkout_url": "http://checkout",
                "status": "pending",
                "payout": {"reference": "PYT-1", "status": "pending"},
            }
        }


class _FakeClient:
    """Capture le payload JSON de la dernière requête POST."""
    captured: dict = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        _FakeClient.captured = json or {}
        return _FakeResp()


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    _FakeClient.captured = {}
    monkeypatch.setattr(gps.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(gps.settings, "GENIUSPAY_WALLET_ID", "wallet-test")


async def test_checkout_envoie_gnf():
    await gps.initier_paiement(
        montant=10_000, description="Livraison", commande_id="c1", partenaire_id="p1",
    )
    assert _FakeClient.captured["currency"] == "GNF"
    assert _FakeClient.captured["amount"] == 10_000  # entier, pas de conversion


async def test_payout_envoie_gnf():
    await gps.initier_payout(
        livreur_id="livreur-1234", montant=5_000, telephone="620000000",
        provider="orange_money", nom_livreur="Sow", idempotency_key="retrait-1",
    )
    assert _FakeClient.captured["currency"] == "GNF"
    assert _FakeClient.captured["amount"] == 5_000


async def test_payout_normalise_le_provider_mtn():
    # L'app envoie `mtn_money`, GeniusPay attend `mtn_momo`.
    await gps.initier_payout(
        livreur_id="livreur-1234", montant=5_000, telephone="620000000",
        provider="mtn_money", nom_livreur="Sow", idempotency_key="retrait-2",
    )
    assert _FakeClient.captured["destination"]["provider"] == "mtn_momo"
