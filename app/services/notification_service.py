import json
from typing import Optional, List
import logging
from ..core.config import settings

logger = logging.getLogger(__name__)


class NotificationService:
    """Service de gestion des notifications"""
    
    def __init__(self):
        self.firebase_app = None
        self._initialize_firebase()
    
    def _initialize_firebase(self):
        """Initialiser Firebase pour les notifications push"""
        try:
            if settings.FIREBASE_CREDENTIALS:
                import firebase_admin
                from firebase_admin import credentials
                
                cred = credentials.Certificate(json.loads(settings.FIREBASE_CREDENTIALS.replace("'", '"')))
                self.firebase_app = firebase_admin.initialize_app(cred)
                logger.info("Firebase initialisé avec succès")
        except Exception as e:
            logger.warning(f"Firebase non initialisé: {e}")
    
    async def envoyer_notification_push(
        self,
        device_token: str,
        titre: str,
        message: str,
        data: Optional[dict] = None
    ) -> bool:
        """
        Envoyer une notification push
        
        Args:
            device_token: Token du device
            titre: Titre de la notification
            message: Message de la notification
            data: Données additionnelles
            
        Returns:
            True si succès, False sinon
        """
        try:
            if not self.firebase_app:
                logger.warning("Firebase non configuré, notification ignorée")
                return False
            
            from firebase_admin import messaging
            
            notification = messaging.Notification(
                title=titre,
                body=message
            )
            
            message_obj = messaging.Message(
                notification=notification,
                data=data or {},
                token=device_token
            )
            
            response = messaging.send(message_obj)
            logger.info(f"Notification envoyée: {response}")
            return True
            
        except Exception as e:
            logger.error(f"Erreur envoi notification: {e}")
            return False
    
    async def notifier_nouvelle_commande(
        self,
        device_tokens: List[str],
        numero_commande: str,
        restaurant_nom: str,
        prix: float,
        distance_km: float
    ):
        """Notifier les livreurs d'une nouvelle commande"""
        titre = "📦 Nouvelle course disponible"
        message = f"{restaurant_nom} - {prix} FCFA - {distance_km:.1f} km"
        
        data = {
            "type": "nouvelle_commande",
            "numero_commande": numero_commande
        }
        
        for token in device_tokens:
            await self.envoyer_notification_push(token, titre, message, data)
    
    async def notifier_commande_acceptee(
        self,
        device_token: str,
        livreur_nom: str,
        numero_commande: str
    ):
        """Notifier le restaurant que la commande a été acceptée"""
        titre = "✅ Course acceptée"
        message = f"{livreur_nom} a accepté la course #{numero_commande}"
        
        data = {
            "type": "commande_acceptee",
            "numero_commande": numero_commande
        }
        
        await self.envoyer_notification_push(device_token, titre, message, data)
    
    async def notifier_changement_status(
        self,
        device_token: str,
        status: str,
        numero_commande: str
    ):
        """Notifier un changement de statut"""
        status_messages = {
            "en_recuperation": "🏪 Le livreur arrive au restaurant",
            "en_livraison": "🏍️ Le livreur est en route",
            "terminee": "✅ Livraison terminée",
            "annulee": "❌ Livraison annulée"
        }
        
        titre = "Mise à jour commande"
        message = f"{status_messages.get(status, status)} - #{numero_commande}"
        
        data = {
            "type": "changement_status",
            "numero_commande": numero_commande,
            "status": status
        }
        
        await self.envoyer_notification_push(device_token, titre, message, data)


# Instance singleton
notification_service = NotificationService()
