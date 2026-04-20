from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
import json
import logging

from ..models.commande import Commande, CommandeStatus
from ..models.livreur import Livreur
from ..schemas.commande import CommandeResponse
from ..services.geolocation_service import GeolocationService
from ..services.notification_service import notification_service
from ..core.config import settings
from ..core.redis import redis_client

logger = logging.getLogger(__name__)


class MatchingService:
    """Service de matching entre commandes et livreurs"""
    
    @staticmethod
    async def diffuser_commande(
        db: AsyncSession,
        commande: Commande,
        partenaire_latitude: float,
        partenaire_longitude: float
    ) -> int:
        """
        Diffuser une commande aux livreurs proches.
        La commande passe TOUJOURS en DIFFUSEE, même si aucun livreur n'est trouvé.
        Les livreurs verront la commande quand ils se connecteront.
        
        Args:
            db: Session de base de données
            commande: Commande à diffuser
            partenaire_latitude: Latitude du partenaire
            partenaire_longitude: Longitude du partenaire
            
        Returns:
            Nombre de livreurs notifiés
        """
        # Passer en DIFFUSEE immédiatement (visible pour tous les livreurs)
        commande.status = CommandeStatus.DIFFUSEE
        commande.diffusee_at = datetime.utcnow()
        await db.commit()
        
        # Trouver les livreurs proches pour les notifier
        livreurs_proches = await GeolocationService.trouver_livreurs_proches(
            db,
            partenaire_latitude,
            partenaire_longitude,
            settings.DEFAULT_SEARCH_RADIUS_KM
        )
        
        # Diffuser en Temps Réel via Redis PubSub (pour le WebSocket admin et livreurs)
        try:
            commande_data = CommandeResponse.model_validate(commande).model_dump(mode='json')
            await redis_client.publish("livraison_ws", json.dumps({
                "target": "livreurs",
                "payload": {
                    "type": "nouvelle_commande",
                    "data": commande_data
                }
            }))
            logger.info(f"Commande {commande.numero_commande} publiée sur Redis PubSub")
        except Exception as e:
            logger.error(f"Erreur publication Redis: {e}")
            
        if not livreurs_proches:
            logger.info(f"Commande {commande.numero_commande} diffusée (aucun livreur à proximité pour le FCM)")
            return 0
        
        # Collecter les tokens pour notification push
        device_tokens = []
        for livreur, distance in livreurs_proches:
            if hasattr(livreur, 'device_token') and livreur.device_token:
                device_tokens.append(livreur.device_token)
        
        # Envoyer les notifications Push Firebase
        if device_tokens:
            await notification_service.notifier_nouvelle_commande(
                device_tokens=device_tokens,
                numero_commande=commande.numero_commande,
                partenaire_nom="Partenaire",
                prix=commande.prix_propose,
                distance_km=livreurs_proches[0][1] if livreurs_proches else 0
            )
        
        logger.info(f"Commande {commande.numero_commande} diffusée à {len(livreurs_proches)} livreurs ({len(device_tokens)} notifiés)")
        return len(livreurs_proches)
    
    @staticmethod
    async def accepter_commande(
        db: AsyncSession,
        commande: Commande,
        livreur: Livreur
    ) -> bool:
        """
        Accepter une commande par un livreur
        
        Args:
            db: Session de base de données
            commande: Commande à accepter
            livreur: Livreur qui accepte
            
        Returns:
            True si succès, False sinon
        """
        # Vérifier que la commande est disponible (CREEE ou DIFFUSEE)
        if commande.status not in (CommandeStatus.CREEE, CommandeStatus.DIFFUSEE):
            logger.warning(f"Commande {commande.numero_commande} déjà acceptée ou invalide (status={commande.status})")
            return False
        
        # Vérifier que le livreur est en ligne
        if not livreur.is_disponible:
            logger.warning(f"Livreur {livreur.id} non disponible")
            return False
        
        # Note: la vérification du nombre max de courses est faite dans l'endpoint
        
        # Assigner la commande
        commande.livreur_id = livreur.id
        commande.status = CommandeStatus.ACCEPTEE
        commande.acceptee_at = datetime.utcnow()
        
        # Marquer le livreur comme en course
        livreur.is_en_course = True
        
        await db.commit()
        
        # Notifier le partenaire
        # Note: Implémenter la récupération du device_token du partenaire
        logger.info(f"Commande {commande.numero_commande} acceptée par livreur {livreur.id}")
        
        return True
    
    @staticmethod
    def calculer_commission(prix_propose: float) -> tuple[float, float]:
        """
        Calculer la commission et le montant pour le livreur
        
        Args:
            prix_propose: Prix proposé pour la livraison
            
        Returns:
            Tuple (commission_plateforme, montant_livreur)
        """
        commission = prix_propose * (settings.PLATFORM_COMMISSION_PERCENTAGE / 100)
        montant_livreur = prix_propose - commission
        
        return round(commission, 2), round(montant_livreur, 2)
