"""Service d'envoi de SMS via PasseInfo (provider local Guinée).

PasseInfo attend les numéros au format `6XXXXXXXX` (9 chiffres, sans indicatif).
On normalise en interne — l'appelant peut passer `+224XXXXXXXXX` ou `224XXXXXXXXX`.
"""
import logging
from typing import Optional

import httpx

from ..core.config import settings

logger = logging.getLogger(__name__)

PASSEINFO_BASE_URL = "https://api.passeinfo.com/v1"


def _to_passeinfo_format(phone: str) -> str:
    """Convertit tout format → `6XXXXXXXX` (9 chiffres, sans indicatif)."""
    p = phone.strip()
    if p.startswith("+224"):
        p = p[4:]
    elif p.startswith("224"):
        p = p[3:]
    elif p.startswith("+"):
        p = p[1:]
    return p  # doit matcher ^6\d{8}$


class SMSService:
    """Service d'envoi de SMS via PasseInfo."""

    def __init__(self):
        self.api_key: Optional[str] = settings.PASSEINFO_API_KEY
        self.client_id: Optional[str] = settings.PASSEINFO_CLIENT_ID
        self._configured = bool(self.api_key and self.client_id)
        if self._configured:
            logger.info("PasseInfo SMS initialisé")
        else:
            logger.warning("PasseInfo SMS non configuré — les SMS seront loggés en dev mode")

    def _headers(self) -> dict:
        return {
            "api-key": self.api_key,
            "client-id": self.client_id,
            "Content-Type": "application/json",
        }

    async def _send(self, telephone: str, message: str) -> bool:
        """Envoi brut. Retourne True si succès."""
        if not self._configured:
            logger.info("[DEV MODE] SMS pour %s: %s", telephone, message)
            return True

        contact = _to_passeinfo_format(telephone)
        payload = {"message": message, "contact": contact}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{PASSEINFO_BASE_URL}/message/single_message",
                    json=payload,
                    headers=self._headers(),
                )
            if response.status_code == 200:
                logger.info("SMS envoyé à %s (id: %s)", telephone, response.json().get("messageId"))
                return True
            logger.error("PasseInfo SMS échec pour %s: %s %s", telephone, response.status_code, response.text)
            return False
        except Exception as e:  # noqa: BLE001
            logger.error("Erreur envoi SMS PasseInfo à %s: %s", telephone, e)
            return False

    # ── API publique ──────────────────────────────────────────────────────

    async def envoyer_otp(self, telephone: str, code_otp: str) -> bool:
        """Envoyer un code OTP à 6 chiffres."""
        message = (
            f"Sönaiyaa — votre code de vérification : {code_otp}\n"
            f"Valide 5 minutes. Ne le partagez avec personne."
        )
        return await self._send(telephone, message)

    async def envoyer_sms_commande(
        self,
        *,
        telephone: str,
        nom_client: str,
        numero_commande: str,
        partenaire_nom: str,
        montant: float,
        tracking_url: str,
        checkout_url: Optional[str] = None,
        position_required: bool = False,
    ) -> bool:
        """
        SMS unifié envoyé au client à la création de la commande.

        3 variantes selon l'état :
        - position_required=True : on a besoin que le client partage sa
          position pour calculer le prix.
        - checkout_url donné : Mobile Money, prix connu, lien paiement +
          tracking dans le SMS.
        - Sinon : Cash, prix connu, lien tracking uniquement.
        """
        prenom = nom_client.split()[0] if nom_client else "Bonjour"

        if position_required:
            message = f"Livraison {partenaire_nom}. Partagez votre position : {tracking_url}"
            return await self._send(telephone, message)

        montant_fmt = f"{int(montant):,}".replace(",", " ") + "GNF"

        if checkout_url:
            message = f"Livraison {partenaire_nom} {montant_fmt}. Payer : {checkout_url}"
        else:
            message = f"Livraison {partenaire_nom} {montant_fmt}. Especes. Suivre : {tracking_url}"

        return await self._send(telephone, message)


# Instance singleton
sms_service = SMSService()
