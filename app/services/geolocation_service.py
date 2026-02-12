from typing import List, Tuple, Optional
from haversine import haversine, Unit
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from ..models.livreur import Livreur
from ..core.config import settings


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
        
        # Récupérer tous les livreurs disponibles avec position connue
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
        
        # Filtrer par distance
        livreurs_proches = []
        point_depart = (latitude, longitude)
        
        for livreur in livreurs:
            point_livreur = (livreur.latitude, livreur.longitude)
            distance = GeolocationService.calculer_distance(point_depart, point_livreur)
            
            if distance <= rayon_km:
                livreurs_proches.append((livreur, distance))
        
        # Trier par distance croissante
        livreurs_proches.sort(key=lambda x: x[1])
        
        return livreurs_proches
    
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
