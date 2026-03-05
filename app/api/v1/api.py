from fastapi import APIRouter
from .endpoints import auth, restaurants, livreurs, commandes, admin, location

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(restaurants.router, prefix="/restaurants", tags=["Restaurants"])
api_router.include_router(livreurs.router, prefix="/livreurs", tags=["Livreurs"])
api_router.include_router(commandes.router, prefix="/commandes", tags=["Commandes"])
api_router.include_router(admin.router, prefix="/admin", tags=["Administration"])
api_router.include_router(location.router, tags=["Location"])
