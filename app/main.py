from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
import logging
import sys
from typing import Dict, Set
from pathlib import Path
from pythonjsonlogger import jsonlogger
from .core.config import settings
from .core.database import init_db, close_db
from .api.v1.api import api_router

# ────────────────────────────────────────────────────────────────────────
# Logging — JSON structuré pour Railway / parsing log aggregators
# ────────────────────────────────────────────────────────────────────────
# Format JSON avec les champs courants utiles en prod (timestamp, level,
# logger name, message, et tous les `extra={"..."}`  passés par les endpoints).
_log_handler = logging.StreamHandler(sys.stdout)
_log_handler.setFormatter(
    jsonlogger.JsonFormatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
    )
)
_root_logger = logging.getLogger()
_root_logger.handlers = [_log_handler]
_root_logger.setLevel(logging.INFO if settings.DEBUG else logging.WARNING)

# Réduire le bruit des loggers tiers
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────
# Sentry — error tracking (activé uniquement si SENTRY_DSN est set)
# ────────────────────────────────────────────────────────────────────────
if settings.SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.ENVIRONMENT,
            release=f"sonaiyaa-backend@{settings.APP_VERSION}",
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
            integrations=[FastApiIntegration(), SqlalchemyIntegration()],
            # `enable_logs` contrôlé par env var `SENTRY_ENABLE_LOGS` :
            # - False (défaut) → mode error only (exceptions + logger.error)
            # - True           → capture aussi logger.warning/info (debug)
            # Bascule via Railway sans redéploiement nécessaire.
            enable_logs=settings.SENTRY_ENABLE_LOGS,
            # Ne pas envoyer les bodies des requêtes (peut contenir des
            # données sensibles : password, tokens, OTP, montants).
            send_default_pii=False,
        )
        # WARNING level — visible même en prod (où le root logger est WARNING+)
        logger.warning(
            "sentry_initialized",
            extra={"environment": settings.ENVIRONMENT, "dsn_present": True},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("sentry_init_failed", extra={"error": str(e)})
else:
    logger.warning("sentry_disabled_no_dsn")

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
        """Boucle de listening Pub/Sub avec reconnexion auto.

        Les serveurs Redis hébergés (Railway / Redis Cloud) ferment les
        connexions Pub/Sub idle au bout de quelques minutes. Sans reconnect,
        on perd silencieusement le broadcast multi-worker.

        Stratégie :
        - On consomme la queue async dans une boucle infinie
        - Sur Exception : log + sleep avec backoff exponentiel
          (1s → 2s → 4s → ... cap à 60s)
        - On recrée la connexion pubsub + on resubscribe au channel
        - `asyncio.CancelledError` (worker stop) → on sort proprement
        """
        backoff = 1  # secondes
        while True:
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
                # Si la boucle sort proprement (rare), reset le backoff
                backoff = 1
            except asyncio.CancelledError:
                logger.info("redis_pubsub_listener_cancelled")
                raise
            except Exception as e:
                logger.warning(
                    "redis_pubsub_listener_error_reconnecting",
                    extra={"error": str(e), "retry_in_seconds": backoff},
                )
                await asyncio.sleep(backoff)
                try:
                    self.pubsub = self.redis.pubsub()
                    await self.pubsub.subscribe("livraison_ws")
                    logger.warning(
                        "redis_pubsub_reconnected",
                        extra={"after_seconds": backoff},
                    )
                    backoff = 1  # reset après reconnect réussi
                except Exception as reconn_err:  # noqa: BLE001
                    logger.error(
                        "redis_pubsub_reconnect_failed",
                        extra={"error": str(reconn_err), "next_retry_in": backoff * 2},
                    )
                    # Exponential backoff capped à 60s
                    backoff = min(backoff * 2, 60)

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

# ────────────────────────────────────────────────────────────────────────
# Rate limiting — slowapi avec backend Redis (partagé entre workers)
# ────────────────────────────────────────────────────────────────────────
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from .core.rate_limit import limiter

app.state.limiter = limiter


async def _rate_limit_handler(request, exc: RateLimitExceeded):
    """Réponse JSON propre quand un client dépasse la limite (429)."""
    from fastapi.responses import JSONResponse
    logger.warning(
        "rate_limit_exceeded",
        extra={
            "path": request.url.path,
            "method": request.method,
            "client_ip": request.client.host if request.client else "unknown",
            "limit": str(exc.detail),
        },
    )
    return JSONResponse(
        status_code=429,
        content={
            "detail": "Trop de requêtes. Réessayez dans un instant.",
        },
    )


app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
app.add_middleware(SlowAPIMiddleware)

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
    """Liveness probe (rapide, ~ms) — utilisé par Railway et les LB.

    Retourne 200 dès que le process Python répond. Ne vérifie PAS les
    dépendances pour ne pas faire trembler le load balancer si Redis a
    une micro-coupure (le worker tourne, c'est suffisant pour rester en
    service). Pour le monitoring fonctionnel, utiliser `/health/deep`.
    """
    from .services.notification_service import notification_service
    return {
        "status": "healthy",
        "version": settings.APP_VERSION,
        "firebase_push": "ok" if notification_service.firebase_app else "disabled",
    }


@app.get("/health/deep")
async def health_deep():
    """Readiness probe (lent, ~50-200ms) — utilisé par UptimeRobot/BetterStack.

    Vérifie les dépendances critiques (DB, Redis, R2). Retourne 503 si
    l'une est down — l'app reste live mais signale qu'elle est en mode
    dégradé, permettant à un monitoring externe de t'alerter.

    GeniusPay n'est pas vérifié ici : c'est une dépendance externe dont
    une coupure transitoire ne doit pas flag Sönaiyaa en degraded.
    """
    from sqlalchemy import text
    from fastapi.responses import JSONResponse
    from .core.redis import redis_client
    from .core.database import async_session_maker
    from .services.storage_service import storage_service
    from .services.notification_service import notification_service

    checks: Dict[str, str] = {}
    overall = "ok"

    # DB
    try:
        async with async_session_maker() as db:
            await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["database"] = f"error: {e!r}"
        overall = "degraded"

    # Redis
    try:
        pong = await redis_client.ping()
        checks["redis"] = "ok" if pong else "no-pong"
        if not pong:
            overall = "degraded"
    except Exception as e:  # noqa: BLE001
        checks["redis"] = f"error: {e!r}"
        overall = "degraded"

    # R2 (head_bucket — léger, juste vérifie creds + reachability)
    try:
        if storage_service.is_ready:
            # boto3 head_bucket est sync — on l'appelle dans un thread
            # pour ne pas bloquer la boucle async sur la latence réseau.
            import asyncio as _asyncio
            await _asyncio.to_thread(
                storage_service._client.head_bucket,
                Bucket=settings.R2_BUCKET_NAME,
            )
            checks["r2"] = "ok"
        else:
            checks["r2"] = "not_configured"
    except Exception as e:  # noqa: BLE001
        checks["r2"] = f"error: {e!r}"
        overall = "degraded"

    # Firebase (juste si le SDK est init, pas de ping réseau)
    checks["firebase"] = "ok" if notification_service.firebase_app else "disabled"

    status_code = 200 if overall == "ok" else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": overall,
            "checks": checks,
            "version": settings.APP_VERSION,
            "environment": settings.ENVIRONMENT,
        },
    )


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

    # Idle timeout : si le client ne ping pas pendant `WS_IDLE_TIMEOUT_S`,
    # on considère la connexion comme morte (crash app, coupure réseau
    # brutale, etc.) et on disconnect — évite l'accumulation de handles
    # zombies côté serveur. Les apps mobiles envoient un ping toutes les
    # ~15-30s en pratique, donc 60s laisse de la marge.
    WS_IDLE_TIMEOUT_S = 60

    try:
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=WS_IDLE_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                logger.info(
                    f"WebSocket idle timeout for user {user_id} "
                    f"(>{WS_IDLE_TIMEOUT_S}s without message)"
                )
                await websocket.close(code=4002, reason="Idle timeout")
                break

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
        logger.info(f"WebSocket disconnected for user {user_id}")
    except Exception as e:
        logger.error(f"WebSocket error for user {user_id}: {e}")
    finally:
        manager.disconnect(user_id)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )
