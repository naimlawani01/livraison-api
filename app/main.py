from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import logging
from typing import Dict, Set
from pathlib import Path
from .core.config import settings
from .core.database import init_db, close_db
from .api.v1.api import api_router

# Configuration du logging
logging.basicConfig(
    level=logging.INFO if settings.DEBUG else logging.WARNING,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Gestionnaire de connexions WebSocket
class ConnectionManager:
    """Gestion des connexions WebSocket en temps réel"""
    
    def __init__(self):
        # Connexions par type d'utilisateur: {user_id: websocket}
        self.active_connections: Dict[str, WebSocket] = {}
        # Groupes de livreurs par zone (pour diffusion géolocalisée)
        self.livreur_connections: Set[str] = set()
    
    async def connect(self, user_id: str, user_type: str, websocket: WebSocket):
        """Connecter un utilisateur"""
        await websocket.accept()
        self.active_connections[user_id] = websocket
        
        if user_type == "livreur":
            self.livreur_connections.add(user_id)
        
        logger.info(f"User {user_id} ({user_type}) connected via WebSocket")
    
    def disconnect(self, user_id: str):
        """Déconnecter un utilisateur"""
        if user_id in self.active_connections:
            del self.active_connections[user_id]
        
        if user_id in self.livreur_connections:
            self.livreur_connections.remove(user_id)
        
        logger.info(f"User {user_id} disconnected")
    
    async def send_personal_message(self, user_id: str, message: dict):
        """Envoyer un message à un utilisateur spécifique"""
        if user_id in self.active_connections:
            websocket = self.active_connections[user_id]
            try:
                await websocket.send_json(message)
            except Exception as e:
                logger.error(f"Error sending message to {user_id}: {e}")
                self.disconnect(user_id)
    
    async def broadcast_to_livreurs(self, message: dict, exclude: str = None):
        """Diffuser un message à tous les livreurs connectés"""
        disconnected = []
        
        for livreur_id in self.livreur_connections:
            if exclude and livreur_id == exclude:
                continue
            
            if livreur_id in self.active_connections:
                try:
                    await self.active_connections[livreur_id].send_json(message)
                except Exception as e:
                    logger.error(f"Error broadcasting to livreur {livreur_id}: {e}")
                    disconnected.append(livreur_id)
        
        # Nettoyer les connexions mortes
        for livreur_id in disconnected:
            self.disconnect(livreur_id)


manager = ConnectionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestion du cycle de vie de l'application"""
    # Startup
    logger.info("Starting application...")
    await init_db()
    logger.info("Database initialized")
    
    yield
    
    # Shutdown
    logger.info("Shutting down application...")
    await close_db()
    logger.info("Database connections closed")


# Créer l'application FastAPI
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    debug=settings.DEBUG,
    lifespan=lifespan
)

# Configuration CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.DEBUG else settings.CORS_ORIGINS,  # Allow all in dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Créer le dossier uploads s'il n'existe pas
Path("uploads/documents").mkdir(parents=True, exist_ok=True)

# Servir les fichiers statiques (documents uploadés)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# Inclure les routes API
app.include_router(api_router, prefix=settings.API_PREFIX)


@app.get("/")
async def root():
    """Page d'accueil de l'API"""
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
        "docs": "/docs",
        "redoc": "/redoc"
    }


@app.get("/health")
async def health_check():
    """Vérification de santé de l'API"""
    return {
        "status": "healthy",
        "version": settings.APP_VERSION
    }


@app.websocket("/ws/{user_id}/{user_type}")
async def websocket_endpoint(websocket: WebSocket, user_id: str, user_type: str):
    """
    Endpoint WebSocket pour les mises à jour en temps réel
    
    user_type: "restaurant", "livreur", "admin"
    """
    await manager.connect(user_id, user_type, websocket)
    
    try:
        while True:
            # Recevoir les messages du client
            data = await websocket.receive_json()
            
            # Traiter les messages selon le type
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            
            elif data.get("type") == "location_update" and user_type == "livreur":
                # Mise à jour de position du livreur
                await manager.send_personal_message(user_id, {
                    "type": "location_updated",
                    "status": "ok"
                })
            
            elif data.get("type") == "nouvelle_commande" and user_type == "restaurant":
                # Diffuser aux livreurs proches
                await manager.broadcast_to_livreurs({
                    "type": "nouvelle_commande",
                    "data": data.get("commande")
                })
            
    except WebSocketDisconnect:
        manager.disconnect(user_id)
        logger.info(f"WebSocket disconnected for user {user_id}")
    except Exception as e:
        logger.error(f"WebSocket error for user {user_id}: {e}")
        manager.disconnect(user_id)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )
