import redis.asyncio as redis
from .config import settings
import logging

logger = logging.getLogger(__name__)

# Connection asynchrone globale pour le Pub/Sub et le Cache
redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

async def get_redis():
    """Dépendance FastAPI pour obtenir le client Redis"""
    return redis_client
