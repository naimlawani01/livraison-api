import json
import os
from typing import Optional, List
import logging
from ..core.config import settings

logger = logging.getLogger(__name__)


class NotificationService:
    """Service de gestion des notifications push via Firebase Cloud Messaging"""
    
    def __init__(self):
        self.firebase_app = None
        self._initialize_firebase()
    
    def _initialize_firebase(self):
        """Initialiser Firebase Admin SDK"""
        try:
            import firebase_admin
            from firebase_admin import credentials

            if firebase_admin._apps:
                self.firebase_app = firebase_admin.get_app()
                logger.info("Firebase déjà initialisé (réutilisation)")
                return

            cred = None

            # Méthode 1 : JSON inline dans FIREBASE_CREDENTIALS
            if settings.FIREBASE_CREDENTIALS:
                try:
                    cred_dict = json.loads(settings.FIREBASE_CREDENTIALS)
                    cred = credentials.Certificate(cred_dict)
                    logger.info("Firebase credentials chargées depuis env (JSON inline)")
                except json.JSONDecodeError:
                    logger.warning("FIREBASE_CREDENTIALS n'est pas un JSON valide")

            # Méthode 2 : Chemin vers un fichier JSON
            if cred is None and settings.FIREBASE_CREDENTIALS_PATH:
                path = settings.FIREBASE_CREDENTIALS_PATH
                if os.path.exists(path):
                    cred = credentials.Certificate(path)
                    logger.info(f"Firebase credentials chargées depuis fichier: {path}")
                else:
                    logger.warning(f"Fichier Firebase introuvable: {path}")

            if cred is not None:
                self.firebase_app = firebase_admin.initialize_app(cred)
                logger.info("Firebase Admin SDK initialisé avec succès")
            else:
                logger.warning("Aucune credentials Firebase trouvées — notifications push désactivées")

        except Exception as e:
            logger.warning(f"Firebase non initialisé: {e}")
    
    async def envoyer_notification_push(
        self,
        device_token: str,
        titre: str,
        message: str,
        data: Optional[dict] = None
    ) -> bool:
        """Envoyer une notification push à un appareil"""
        try:
            if not self.firebase_app:
                logger.debug("Firebase non configuré, notification ignorée")
                return False
            
            from firebase_admin import messaging
            
            notification = messaging.Notification(
                title=titre,
                body=message
            )
            
            str_data = {k: str(v) for k, v in (data or {}).items()}
            
            message_obj = messaging.Message(
                notification=notification,
                data=str_data,
                token=device_token
            )
            
            response = messaging.send(message_obj)
            logger.info(f"Notification envoyée: {response}")
            return True
            
        except Exception as e:
            logger.error(f"Erreur envoi notification: {e}")
            return False

    async def envoyer_a_plusieurs(
        self,
        device_tokens: List[str],
        titre: str,
        message: str,
        data: Optional[dict] = None
    ) -> int:
        """Envoyer une notification à plusieurs appareils. Retourne le nombre de succès."""
        if not self.firebase_app or not device_tokens:
            return 0

        sent = 0
        for token in device_tokens:
            if await self.envoyer_notification_push(token, titre, message, data):
                sent += 1
        return sent
    
    async def notifier_nouvelle_commande(
        self,
        device_tokens: List[str],
        numero_commande: str,
        partenaire_nom: str,
        prix: float,
        distance_km: float
    ):
        """Notifier les livreurs d'une nouvelle commande disponible"""
        titre = f"Nouvelle course — {prix:.0f} FCFA"
        msg = f"{partenaire_nom} · à {distance_km:.1f} km"
        data = {"type": "nouvelle_commande", "numero_commande": numero_commande}

        count = await self.envoyer_a_plusieurs(device_tokens, titre, msg, data)
        logger.info(f"Notification nouvelle commande envoyée à {count}/{len(device_tokens)} livreurs")
    
    async def notifier_commande_acceptee(
        self,
        device_token: str,
        livreur_nom: str,
        numero_commande: str
    ):
        """Notifier le partenaire que sa commande a été acceptée"""
        titre = "Course acceptée"
        msg = f"{livreur_nom} a accepté la course #{numero_commande}"
        data = {"type": "commande_acceptee", "numero_commande": numero_commande}
        
        await self.envoyer_notification_push(device_token, titre, msg, data)
    
    async def notifier_changement_status(
        self,
        device_token: str,
        status: str,
        numero_commande: str
    ):
        """Notifier un changement de statut de commande"""
        status_notifs = {
            "EN_RECUPERATION": ("Livreur en route", "Votre livreur se dirige vers le commerce"),
            "EN_LIVRAISON":    ("Livraison en cours", "Le livreur est en route vers le client"),
            "TERMINEE":        ("Livraison terminée !", "La commande a bien été livrée"),
            "ANNULEE":         ("Course annulée", "La livraison a été annulée"),
        }

        titre, msg = status_notifs.get(status, ("Mise à jour", f"Statut : {status}"))
        data = {"type": "changement_status", "numero_commande": numero_commande, "status": status}

        await self.envoyer_notification_push(device_token, titre, msg, data)


notification_service = NotificationService()
