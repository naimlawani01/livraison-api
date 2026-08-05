from fastapi import APIRouter
from .endpoints import auth, expediteurs, livreurs, commandes, admin, location, tracking, wallet, payments, credit

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(expediteurs.router, prefix="/expediteurs", tags=["Expediteurs"])
api_router.include_router(credit.router, prefix="/expediteurs", tags=["Crédit"])
api_router.include_router(livreurs.router, prefix="/livreurs", tags=["Livreurs"])
api_router.include_router(wallet.router, prefix="/livreurs", tags=["Wallet"])
api_router.include_router(commandes.router, prefix="/commandes", tags=["Commandes"])
api_router.include_router(admin.router, prefix="/admin", tags=["Administration"])
api_router.include_router(location.router, tags=["Location"])
api_router.include_router(tracking.router, tags=["Tracking"])
api_router.include_router(payments.router, prefix="/payments", tags=["Paiements"])
