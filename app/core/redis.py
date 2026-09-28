import redis.asyncio as redis
from .config import settings
import logging

logger = logging.getLogger(__name__)

# Connection asynchrone globale pour le Pub/Sub et le Cache
# socket_connect_timeout : si Redis est injoignable, échouer en 2 s (au lieu de
# bloquer la requête) → les replis prévus par chaque appelant prennent le relais.
# Pas de socket_timeout : il couperait l'écoute Pub/Sub (idle légitime).
redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=2)

async def get_redis():
    """Dépendance FastAPI pour obtenir le client Redis"""
    return redis_client
