"""
Service d'intégration GeniusPay.

Collecte  : POST /api/v1/merchant/payments   (X-API-Key + X-API-Secret)
Payout    : POST /api/v1/merchant/payouts    (Authorization: Bearer TOKEN)
Webhook   : vérification HMAC-SHA256(timestamp.payload, whsec_xxx)
"""
import hashlib
import hmac
import logging
import time
from typing import Optional

import httpx

from ..core.config import settings

logger = logging.getLogger(__name__)

BASE_URL = settings.GENIUSPAY_BASE_URL


# ── Helpers ──────────────────────────────────────────────────────────────────

def _collect_headers() -> dict:
    return {
        "X-API-Key": settings.GENIUSPAY_API_KEY,
        "X-API-Secret": settings.GENIUSPAY_API_SECRET,
        "Content-Type": "application/json",
    }


def _payout_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.GENIUSPAY_API_KEY}",
        "Content-Type": "application/json",
    }


# ── 1. Initier un paiement (collecte partenaire) ─────────────────────────────

async def initier_paiement(
    *,
    commande_id: str,
    partenaire_id: str,
    montant: float,
    description: str,
    telephone_client: Optional[str] = None,
    nom_client: Optional[str] = None,
    success_url: Optional[str] = None,
    error_url: Optional[str] = None,
) -> dict:
    """
    Crée une transaction GeniusPay et retourne:
      - checkout_url  : URL à ouvrir dans l'app
      - reference     : MTX-xxx à stocker sur la commande
    
    En mode sandbox les clés pk_sandbox_xxx / sk_sandbox_xxx suffisent.
    """
    payload: dict = {
        "amount": int(montant),          # GeniusPay attend un entier
        "description": description[:500],
        "metadata": {
            "commande_id": commande_id,
            "partenaire_id": partenaire_id,
        },
    }

    if telephone_client:
        payload["customer"] = {
            "name": nom_client or "",
            "phone": telephone_client,
        }

    if success_url:
        payload["success_url"] = success_url
    if error_url:
        payload["error_url"] = error_url

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{BASE_URL}/payments",
            headers=_collect_headers(),
            json=payload,
        )

    if resp.status_code not in (200, 201):
        logger.error("GeniusPay initier_paiement error %s: %s", resp.status_code, resp.text)
        raise GeniusPayError(f"GeniusPay erreur {resp.status_code}: {resp.text}")

    data = resp.json().get("data", {})
    return {
        "reference": data.get("reference"),
        "checkout_url": data.get("checkout_url") or data.get("payment_url"),
        "status": data.get("status", "pending"),
    }


# ── 2. Vérifier la signature d'un webhook ────────────────────────────────────

def verify_webhook_signature(
    payload_bytes: bytes,
    signature: str,
    timestamp: str,
    *,
    max_age_seconds: int = 300,
) -> bool:
    """
    Vérifie la signature HMAC-SHA256 du webhook GeniusPay.
    Format : HMAC-SHA256(timestamp + "." + json_payload, whsec_xxx)
    Rejette les webhooks > 5 min (protection replay attack).
    """
    secret = settings.GENIUSPAY_WEBHOOK_SECRET
    if not secret:
        logger.warning("GENIUSPAY_WEBHOOK_SECRET non configuré — webhook non vérifié")
        return False

    # Protection replay
    try:
        if abs(time.time() - int(timestamp)) > max_age_seconds:
            logger.warning("Webhook GeniusPay trop ancien (timestamp: %s)", timestamp)
            return False
    except (ValueError, TypeError):
        return False

    data = f"{timestamp}.{payload_bytes.decode()}"
    expected = hmac.new(
        secret.encode(),
        data.encode(),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


# ── 3. Initier un payout (retrait livreur) ───────────────────────────────────

async def initier_payout(
    *,
    livreur_id: str,
    montant: float,
    telephone: str,
    provider: str,          # "orange_money" | "mtn_money" | "wave" | ...
    nom_livreur: str,
    idempotency_key: str,   # ex: f"retrait-{transaction_id}"
) -> dict:
    """
    Déclenche un payout vers le Mobile Money du livreur.
    Retourne {"reference": "PYT-xxx", "status": "pending"}.
    """
    if not settings.GENIUSPAY_WALLET_ID:
        raise GeniusPayError("GENIUSPAY_WALLET_ID non configuré")

    payload = {
        "wallet_id": settings.GENIUSPAY_WALLET_ID,
        "recipient": {
            "name": nom_livreur,
            "phone": telephone,
        },
        "destination": {
            "type": "mobile_money",
            "provider": provider,
            "account": telephone,
        },
        "amount": int(montant),
        "description": f"Retrait Sönaiya — livreur {livreur_id[:8]}",
        "metadata": {
            "livreur_id": livreur_id,
        },
        "idempotency_key": idempotency_key,
    }

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{BASE_URL}/payouts",
            headers=_payout_headers(),
            json=payload,
        )

    if resp.status_code not in (200, 201):
        logger.error("GeniusPay payout error %s: %s", resp.status_code, resp.text)
        raise GeniusPayError(f"GeniusPay payout erreur {resp.status_code}: {resp.text}")

    data = resp.json().get("data", {}).get("payout", {})
    return {
        "reference": data.get("reference"),
        "status": data.get("status", "pending"),
    }


# ── 4. Récupérer le statut d'un paiement ─────────────────────────────────────

async def get_paiement(reference: str) -> dict:
    """Vérifie le statut d'une transaction (polling de secours)."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{BASE_URL}/payments/{reference}",
            headers=_collect_headers(),
        )
    if resp.status_code != 200:
        raise GeniusPayError(f"GeniusPay get_paiement erreur {resp.status_code}")
    return resp.json().get("data", {})


# ── Erreur custom ─────────────────────────────────────────────────────────────

class GeniusPayError(Exception):
    pass
