"""Service d'envoi de SMS via Nimba SMS (provider local Guinée).

Nimba attend les numéros au format `224XXXXXXXXX` (sans `+`). On normalise
en interne — l'appelant peut passer le format `+224XXXXXXXXX` sans souci.
"""
import logging
from typing import Optional

from nimbasms import Client

from ..core.config import settings

logger = logging.getLogger(__name__)


def _to_nimba_format(phone: str) -> str:
    """Convertit `+224XXXXXXXXX` → `224XXXXXXXXX` (Nimba veut sans `+`)."""
    if phone.startswith("+"):
        return phone[1:]
    return phone


class SMSService:
    """Service d'envoi de SMS via Nimba."""

    def __init__(self):
        self.client: Optional[Client] = None
        self.sender_name: str = settings.NIMBASMS_SENDER_NAME
        self._initialize()

    def _initialize(self):
        if not (settings.NIMBASMS_ACCOUNT_SID and settings.NIMBASMS_AUTH_TOKEN):
            logger.warning("Nimba SMS non configuré — les SMS seront loggés en dev mode")
            return
        try:
            self.client = Client(
                settings.NIMBASMS_ACCOUNT_SID,
                settings.NIMBASMS_AUTH_TOKEN,
            )
            logger.info("Nimba SMS initialisé (sender: %s)", self.sender_name)
        except Exception as e:  # noqa: BLE001
            logger.warning("Nimba SMS init failed: %s", e)

    # ── Helper interne ────────────────────────────────────────────────────

    def _send(self, telephone: str, message: str) -> bool:
        """Envoi brut. Retourne True si succès."""
        if not self.client:
            logger.info("[DEV MODE] SMS pour %s: %s", telephone, message)
            return True

        try:
            response = self.client.messages.create(
                to=[_to_nimba_format(telephone)],
                sender_name=self.sender_name,
                message=message,
            )
            if response.ok:
                logger.info("SMS envoyé à %s", telephone)
                return True
            logger.error("SMS Nimba échec pour %s: %s", telephone, response.data)
            return False
        except Exception as e:  # noqa: BLE001
            logger.error("Erreur envoi SMS Nimba à %s: %s", telephone, e)
            return False

    # ── API publique ──────────────────────────────────────────────────────

    async def envoyer_otp(self, telephone: str, code_otp: str) -> bool:
        """Envoyer un code OTP à 6 chiffres."""
        message = (
            f"Sönaiya — votre code de vérification : {code_otp}\n"
            f"Valide 5 minutes. Ne le partagez avec personne."
        )
        return self._send(telephone, message)

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
    ) -> bool:
        """
        SMS unifié envoyé au client à la création de la commande.

        Contient :
        - Lien de suivi (toujours)
        - Lien de paiement (si Mobile Money)
        """
        prenom = nom_client.split()[0] if nom_client else "Bonjour"
        montant_fmt = f"{int(montant):,}".replace(",", " ") + " GNF"

        if checkout_url:
            message = (
                f"Bonjour {prenom}, votre commande {numero_commande} chez "
                f"{partenaire_nom} ({montant_fmt}) est en cours.\n"
                f"Payer : {checkout_url}\n"
                f"Suivre : {tracking_url}"
            )
        else:
            message = (
                f"Bonjour {prenom}, votre commande {numero_commande} chez "
                f"{partenaire_nom} ({montant_fmt}) est en cours.\n"
                f"À régler en espèces à la livraison.\n"
                f"Suivre : {tracking_url}"
            )

        return self._send(telephone, message)


# Instance singleton
sms_service = SMSService()
