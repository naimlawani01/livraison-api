from typing import Optional
import logging
from twilio.rest import Client
from ..core.config import settings

logger = logging.getLogger(__name__)


class SMSService:
    """Service d'envoi de SMS (OTP)"""
    
    def __init__(self):
        self.client = None
        self._initialize_twilio()
    
    def _initialize_twilio(self):
        """Initialiser le client Twilio"""
        try:
            if settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN:
                self.client = Client(
                    settings.TWILIO_ACCOUNT_SID,
                    settings.TWILIO_AUTH_TOKEN
                )
                logger.info("Twilio initialisé avec succès")
        except Exception as e:
            logger.warning(f"Twilio non initialisé: {e}")
    
    async def envoyer_otp(self, telephone: str, code_otp: str) -> bool:
        """
        Envoyer un code OTP par SMS
        
        Args:
            telephone: Numéro de téléphone (format international)
            code_otp: Code OTP à 6 chiffres
            
        Returns:
            True si succès, False sinon
        """
        try:
            if not self.client:
                # En mode dev, log le code au lieu d'envoyer
                logger.info(f"[DEV MODE] Code OTP pour {telephone}: {code_otp}")
                return True
            
            message = f"Votre code de vérification est: {code_otp}\n\nCode valide 5 minutes."
            
            result = self.client.messages.create(
                body=message,
                from_=settings.TWILIO_PHONE_NUMBER,
                to=telephone
            )
            
            logger.info(f"SMS envoyé à {telephone}, SID: {result.sid}")
            return True
            
        except Exception as e:
            logger.error(f"Erreur envoi SMS: {e}")
            return False

    async def envoyer_lien_paiement(
        self,
        telephone: str,
        nom_client: str,
        checkout_url: str,
        numero_commande: str,
        montant: float,
    ) -> bool:
        """
        Envoyer le lien de paiement GeniusPay au client final par SMS.
        Le client n'a pas d'app — il paie depuis ce lien.
        """
        try:
            message = (
                f"Bonjour {nom_client},\n"
                f"Votre commande {numero_commande} est prête.\n"
                f"Montant à payer : {int(montant):,} GNF\n"
                f"Payez en ligne ici : {checkout_url}\n"
                f"La livraison démarrera après votre paiement."
            )

            if not self.client:
                logger.info("[DEV MODE] SMS lien paiement pour %s: %s", telephone, checkout_url)
                return True

            result = self.client.messages.create(
                body=message,
                from_=settings.TWILIO_PHONE_NUMBER,
                to=telephone,
            )
            logger.info("SMS lien paiement envoyé à %s, SID: %s", telephone, result.sid)
            return True

        except Exception as e:
            logger.error("Erreur envoi SMS lien paiement: %s", e)
            return False


# Instance singleton
sms_service = SMSService()
