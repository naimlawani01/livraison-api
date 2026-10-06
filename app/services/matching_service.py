from typing import List, Optional
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone
import json
import logging

from ..models.course import Course, CourseStatus
from ..models.livreur import Livreur
from ..schemas.course import CourseResponse
from ..services.geolocation_service import GeolocationService
from ..services.notification_service import notification_service
from ..core.config import settings
from ..core.redis import redis_client

logger = logging.getLogger(__name__)


class MatchingService:
    """Service de matching entre courses et livreurs"""
    
    @staticmethod
    async def diffuser_course(
        db: AsyncSession,
        course: Course,
        expediteur_latitude: float,
        expediteur_longitude: float,
        expediteur_nom: str = "Commerce"
    ) -> int:
        """
        Diffuser une course aux livreurs proches.
        La course passe TOUJOURS en DIFFUSEE, même si aucun livreur n'est trouvé.
        Les livreurs verront la course quand ils se connecteront.
        
        Args:
            db: Session de base de données
            course: Course à diffuser
            expediteur_latitude: Latitude du expediteur
            expediteur_longitude: Longitude du expediteur
            
        Returns:
            Nombre de livreurs notifiés
        """
        # Ne jamais ressusciter une course acceptée, terminée ou annulée (ex. webhook
        # de paiement reçu après annulation).
        if course.status not in (CourseStatus.CREEE, CourseStatus.DIFFUSEE):
            logger.warning(
                "Diffusion ignorée : course non diffusable",
                extra={"course_id": str(course.id), "status": str(course.status)},
            )
            return 0

        # Passer en DIFFUSEE immédiatement (visible pour tous les livreurs)
        course.status = CourseStatus.DIFFUSEE
        course.diffusee_at = datetime.now(timezone.utc)
        await db.commit()
        
        # Trouver les livreurs proches pour les notifier
        livreurs_proches = await GeolocationService.trouver_livreurs_proches(
            db,
            expediteur_latitude,
            expediteur_longitude,
            settings.DEFAULT_SEARCH_RADIUS_KM
        )
        
        # Diffuser en Temps Réel via Redis PubSub (pour le WebSocket admin et livreurs)
        try:
            from ..api.v1.endpoints.courses import masquer_client_avant_acceptation
            # Diffusé à TOUS les livreurs proches : pas de données personnelles
            # du client ni de jetons (cf. masquer_client_avant_acceptation).
            course_data = masquer_client_avant_acceptation(
                CourseResponse.model_validate(course).model_dump(mode='json')
            )
            await redis_client.publish("livraison_ws", json.dumps({
                "target": "livreurs",
                "payload": {
                    "type": "nouvelle_course",
                    "data": course_data
                }
            }))
            logger.info(f"Course {course.numero_course} publiée sur Redis PubSub")
        except Exception as e:
            logger.error(f"Erreur publication Redis: {e}")
            
        if not livreurs_proches:
            logger.info(f"Course {course.numero_course} diffusée (aucun livreur à proximité pour le FCM)")
            return 0
        
        # Collecter les tokens pour notification push
        device_tokens = []
        for livreur, distance in livreurs_proches:
            if hasattr(livreur, 'device_token') and livreur.device_token:
                device_tokens.append(livreur.device_token)
        
        # Envoyer les notifications Push Firebase
        if device_tokens:
            await notification_service.notifier_nouvelle_course(
                device_tokens=device_tokens,
                numero_course=course.numero_course,
                expediteur_nom=expediteur_nom,
                prix=course.prix_propose,
                distance_km=livreurs_proches[0][1] if livreurs_proches else 0
            )
        
        logger.info(f"Course {course.numero_course} diffusée à {len(livreurs_proches)} livreurs ({len(device_tokens)} notifiés)")
        return len(livreurs_proches)
    
    @staticmethod
    async def accepter_course(
        db: AsyncSession,
        course: Course,
        livreur: Livreur
    ) -> bool:
        """
        Accepter une course par un livreur
        
        Args:
            db: Session de base de données
            course: Course à accepter
            livreur: Livreur qui accepte
            
        Returns:
            True si succès, False sinon
        """
        # Vérifier que la course est disponible (CREEE ou DIFFUSEE)
        if course.status != CourseStatus.DIFFUSEE:
            logger.warning(f"Course {course.numero_course} déjà acceptée ou invalide (status={course.status})")
            return False
        
        # Vérifier que le livreur est en ligne
        if not livreur.is_disponible:
            logger.warning(f"Livreur {livreur.id} non disponible")
            return False
        
        # Note: la vérification du nombre max de courses est faite dans l'endpoint
        
        # Assigner la course — UPDATE conditionnel ATOMIQUE : si deux livreurs
        # acceptent à la même seconde, un seul voit rowcount == 1, l'autre perd.
        # (Avant : lecture puis écriture → les deux « gagnaient ».)
        numero = course.numero_course  # lu avant un éventuel rollback (qui expire l'objet)
        res = await db.execute(
            update(Course)
            .where(Course.id == course.id, Course.status == CourseStatus.DIFFUSEE)
            .values(
                livreur_id=livreur.id,
                status=CourseStatus.ACCEPTEE,
                acceptee_at=datetime.now(timezone.utc),
            )
            .execution_options(synchronize_session=False)
        )
        if res.rowcount != 1:
            await db.rollback()
            logger.warning(f"Course {numero} prise par un autre livreur entre-temps")
            return False

        # Marquer le livreur comme en course
        livreur.is_en_course = True

        await db.commit()
        await db.refresh(course)
        
        # Notifier le expediteur
        # Note: Implémenter la récupération du device_token du expediteur
        logger.info(f"Course {course.numero_course} acceptée par livreur {livreur.id}")
        
        return True
