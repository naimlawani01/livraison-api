from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
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

# Réduire le bruit des loggers tiers
# - uvicorn.access : log toutes les requêtes HTTP (health checks, polling) → WARNING
# - httpx / urllib3 : log les requêtes sortantes (Twilio, GeniusPay, Firebase) → WARNING
# - sqlalchemy.engine : pour le cas où SQLALCHEMY_ECHO serait activé par erreur
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

import asyncio
import json

# Gestionnaire de connexions WebSocket avec Redis Pub/Sub
class ConnectionManager:
    """Gestion des connexions WebSocket en temps réel sur plusieurs workers"""
    
    def __init__(self):
        # Connexions par type d'utilisateur local: {user_id: websocket}
        self.active_connections: Dict[str, WebSocket] = {}
        # Groupes de livreurs locaux
        self.livreur_connections: Set[str] = set()
        self.pubsub = None
        self.redis = None
    
    async def initialize(self, redis_client):
        self.redis = redis_client
        self.pubsub = self.redis.pubsub()
        await self.pubsub.subscribe("livraison_ws")
        asyncio.create_task(self._listen_to_redis())

    async def _listen_to_redis(self):
        """Tâche asynchrone pour recevoir les messages de Redis"""
        try:
            async for message in self.pubsub.listen():
                if message["type"] == "message":
                    data = json.loads(message["data"])
                    target = data.get("target")
                    payload = data.get("payload")
                    
                    if target == "livreurs":
                        await self._local_broadcast_to_livreurs(payload)
                    elif target.startswith("user:"):
                        user_id = target.split(":")[1]
                        await self._local_send_personal_message(user_id, payload)
        except Exception as e:
            logger.error(f"Redis PubSub listener error: {e}")

    async def connect(self, user_id: str, user_type: str, websocket: WebSocket):
        """Connecter un utilisateur localement au worker actuel"""
        await websocket.accept()
        self.active_connections[user_id] = websocket
        
        if user_type == "livreur":
            self.livreur_connections.add(user_id)
        
        logger.info(f"User {user_id} ({user_type}) connected via WebSocket")
    
    def disconnect(self, user_id: str):
        if user_id in self.active_connections:
            del self.active_connections[user_id]
        if user_id in self.livreur_connections:
            self.livreur_connections.remove(user_id)
        logger.info(f"User {user_id} disconnected")
    
    async def _local_send_personal_message(self, user_id: str, message: dict):
        if user_id in self.active_connections:
            websocket = self.active_connections[user_id]
            try:
                await websocket.send_json(message)
            except Exception as e:
                logger.error(f"Error sending message to {user_id}: {e}")
                self.disconnect(user_id)

    async def send_personal_message(self, user_id: str, message: dict):
        """Publier un message ciblé sur Redis pour atteindre le bon worker"""
        if self.redis:
            await self.redis.publish("livraison_ws", json.dumps({
                "target": f"user:{user_id}",
                "payload": message
            }))
    
    async def _local_broadcast_to_livreurs(self, message: dict):
        disconnected = []
        for livreur_id in self.livreur_connections:
            try:
                await self.active_connections[livreur_id].send_json(message)
            except Exception as e:
                logger.error(f"Error broadcasting to livreur {livreur_id}: {e}")
                disconnected.append(livreur_id)
        for livreur_id in disconnected:
            self.disconnect(livreur_id)

    async def broadcast_to_livreurs(self, message: dict, exclude: str = None):
        """Publier un message à tous les livreurs via Redis"""
        if self.redis:
            await self.redis.publish("livraison_ws", json.dumps({
                "target": "livreurs",
                "payload": message
            }))


manager = ConnectionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestion du cycle de vie de l'application"""
    # Startup
    logger.warning("Starting application...")
    await init_db()
    logger.warning("Database initialized")

    # Initialize Redis Pub/Sub
    from .core.redis import redis_client
    await manager.initialize(redis_client)
    logger.warning("Redis ConnectionManager initialized")

    # Vérifier Firebase Admin SDK
    from .services.notification_service import notification_service
    if notification_service.firebase_app:
        logger.warning("[Firebase] Admin SDK OK — push notifications actives")
    else:
        logger.warning("[Firebase] ATTENTION : Admin SDK non configuré — push notifications DÉSACTIVÉES. Ajouter FIREBASE_CREDENTIALS dans les variables d'environnement.")
    
    yield
    
    # Shutdown
    logger.info("Shutting down application...")
    await close_db()
    
    if manager.pubsub:
        await manager.pubsub.close()
    await redis_client.aclose()
    logger.info("Redis connections closed")


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
    allow_origins=["*"] if settings.CORS_ALLOW_ALL_ORIGINS else settings.CORS_ORIGINS,
    allow_credentials=not settings.CORS_ALLOW_ALL_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Créer le dossier uploads s'il n'existe pas
Path("uploads/documents").mkdir(parents=True, exist_ok=True)

# Servir les fichiers statiques (documents uploadés)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# Pages légales — chemin absolu relatif à ce fichier (/app/app/main.py → /app/static/pages/)
_static_pages = Path(__file__).parent.parent / "static" / "pages"

@app.get("/politique-confidentialite", response_class=HTMLResponse, include_in_schema=False)
async def politique_confidentialite():
    return (_static_pages / "politique-confidentialite.html").read_text(encoding="utf-8")

@app.get("/conditions-utilisation", response_class=HTMLResponse, include_in_schema=False)
async def conditions_utilisation():
    return (_static_pages / "conditions-utilisation.html").read_text(encoding="utf-8")

# Inclure les routes API
app.include_router(api_router, prefix=settings.API_PREFIX)

# Routes publiques (pas de prefix API, pas d'auth)
from .api.v1.endpoints.location import router as location_public_router
from .api.v1.endpoints.tracking import router as tracking_public_router
app.include_router(location_public_router, tags=["Location (public)"], include_in_schema=False)
app.include_router(tracking_public_router, tags=["Tracking (public)"], include_in_schema=False)


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
    from .services.notification_service import notification_service
    return {
        "status": "healthy",
        "version": settings.APP_VERSION,
        "firebase_push": "ok" if notification_service.firebase_app else "disabled"
    }


@app.websocket("/ws/{user_id}/{user_type}")
async def websocket_endpoint(websocket: WebSocket, user_id: str, user_type: str):
    """
    Endpoint WebSocket pour les mises à jour en temps réel
    
    user_type: "partenaire", "livreur", "admin"
    Authentification via query param : /ws/{user_id}/{user_type}?token=xxx
    """
    # Vérifier le JWT avant d'accepter la connexion
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="Token manquant")
        return
    
    try:
        from .core.security import decode_token
        payload = decode_token(token)
        token_user_id = payload.get("sub")
        if token_user_id != user_id:
            await websocket.close(code=4003, reason="Token ne correspond pas")
            return
    except Exception:
        await websocket.close(code=4001, reason="Token invalide")
        return
    
    await manager.connect(user_id, user_type, websocket)
    
    try:
        while True:
            data = await websocket.receive_json()
            
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            
            elif data.get("type") == "location_update" and user_type == "livreur":
                await manager.send_personal_message(user_id, {
                    "type": "location_updated",
                    "status": "ok"
                })
            
            elif data.get("type") == "nouvelle_commande" and user_type == "partenaire":
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
