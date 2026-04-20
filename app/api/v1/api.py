from fastapi import APIRouter
from .endpoints import auth, partenaires, livreurs, commandes, admin, location, tracking

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(partenaires.router, prefix="/partenaires", tags=["Partenaires"])
api_router.include_router(livreurs.router, prefix="/livreurs", tags=["Livreurs"])
api_router.include_router(commandes.router, prefix="/commandes", tags=["Commandes"])
api_router.include_router(admin.router, prefix="/admin", tags=["Administration"])
api_router.include_router(location.router, tags=["Location"])
api_router.include_router(tracking.router, tags=["Tracking"])
