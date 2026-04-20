from typing import List, Tuple, Optional
from haversine import haversine, Unit
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from ..models.livreur import Livreur
from ..core.config import settings
from ..core.redis import redis_client
import logging

logger = logging.getLogger(__name__)


class GeolocationService:
    """Service de géolocalisation"""
    
    @staticmethod
    def calculer_distance(
        point1: Tuple[float, float],
        point2: Tuple[float, float]
    ) -> float:
        """
        Calculer la distance entre deux points GPS en kilomètres
        
        Args:
            point1: Tuple (latitude, longitude)
            point2: Tuple (latitude, longitude)
            
        Returns:
            Distance en kilomètres
        """
        return haversine(point1, point2, unit=Unit.KILOMETERS)
    
    @staticmethod
    async def trouver_livreurs_proches(
        db: AsyncSession,
        latitude: float,
        longitude: float,
        rayon_km: Optional[float] = None
    ) -> List[Tuple[Livreur, float]]:
        """
        Trouver les livreurs disponibles dans un rayon donné
        
        Args:
            db: Session de base de données
            latitude: Latitude du point de départ
            longitude: Longitude du point de départ
            rayon_km: Rayon de recherche (par défaut: DEFAULT_SEARCH_RADIUS_KM)
            
        Returns:
            Liste de tuples (Livreur, distance_km) triée par distance
        """
        if rayon_km is None:
            rayon_km = settings.DEFAULT_SEARCH_RADIUS_KM
            
        livreurs_proches_result = []
        
        try:
            # 1. Requête ultra-rapide en mémoire (Redis)
            # Retourne une liste de la forme [[b'id1', 2.45], [b'id2', 5.12]]
            redis_results = await redis_client.georadius(
                "livreurs_locations",
                longitude,
                latitude,
                rayon_km,
                unit='km',
                withdist=True
            )
            
            if not redis_results:
                return []
                
            # Extraire les IDs et correspondances de distances
            distance_map = {}
            livreur_ids = []
            for item in redis_results:
                l_id = item[0]
                dist = item[1]
                distance_map[l_id] = dist
                livreur_ids.append(l_id)
                
            # 2. Récupérer uniquement les informations des livreurs trouvés dans PostgreSQL
            if livreur_ids:
                query = select(Livreur).where(
                    and_(
                        Livreur.id.in_(livreur_ids),
                        Livreur.is_disponible == True,
                        Livreur.is_en_course == False,
                        Livreur.is_verified == True
                    )
                )
                
                result = await db.execute(query)
                livreurs = result.scalars().all()
                
                for livreur in livreurs:
                    # Remplacer la position obsolète de Postgres par la position en temps réel de Redis (Optionnel mais précis)
                    dist = distance_map.get(str(livreur.id))
                    if dist is not None:
                        livreurs_proches_result.append((livreur, dist))
                        
                # Trier par distance croissante
                livreurs_proches_result.sort(key=lambda x: x[1])
                
        except Exception as e:
            logger.error(f"Erreur d'accès au cache géospatial Redis: {e}")
            # Mode dégradé (Fallback) vers PostgreSQL classique si Redis plante
            query = select(Livreur).where(
                and_(
                    Livreur.is_disponible == True,
                    Livreur.is_en_course == False,
                    Livreur.is_verified == True,
                    Livreur.latitude.isnot(None),
                    Livreur.longitude.isnot(None)
                )
            )
            result = await db.execute(query)
            livreurs = result.scalars().all()
            
            point_depart = (latitude, longitude)
            for livreur in livreurs:
                distance = GeolocationService.calculer_distance(point_depart, (livreur.latitude, livreur.longitude))
                if distance <= rayon_km:
                    livreurs_proches_result.append((livreur, distance))
            
            livreurs_proches_result.sort(key=lambda x: x[1])
            
        return livreurs_proches_result
    
    @staticmethod
    def estimer_duree_trajet(distance_km: float, vitesse_moyenne_kmh: float = 30.0) -> int:
        """
        Estimer la durée d'un trajet en minutes
        
        Args:
            distance_km: Distance en kilomètres
            vitesse_moyenne_kmh: Vitesse moyenne (par défaut 30 km/h pour moto en ville)
            
        Returns:
            Durée estimée en minutes
        """
        if distance_km <= 0:
            return 0
        
        heures = distance_km / vitesse_moyenne_kmh
        minutes = heures * 60
        
        return max(5, int(minutes))  # Minimum 5 minutes
    
    @staticmethod
    def valider_coordonnees(latitude: float, longitude: float) -> bool:
        """
        Valider que les coordonnées GPS sont valides
        
        Args:
            latitude: Latitude
            longitude: Longitude
            
        Returns:
            True si valide, False sinon
        """
        return (-90 <= latitude <= 90) and (-180 <= longitude <= 180)
