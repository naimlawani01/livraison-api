"""Rate limiting global avec slowapi.

Backend : la même instance Redis qu'on utilise déjà pour les WebSockets et
le cache. Permet à plusieurs workers Railway (uvicorn workers) de partager
l'état du compteur — sinon chaque worker aurait son propre compteur en
mémoire et un attaquant pourrait multiplier les requêtes par N workers.

Utilisation :
    from app.core.rate_limit import limiter

    @router.post("/login")
    @limiter.limit("5/minute")
    async def login(request: Request, ...):
        ...

⚠️ `request: Request` doit être présent comme premier paramètre — slowapi
en a besoin pour extraire l'IP du client.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

from .config import settings


def _key_func(request) -> str:
    """Identifie un client pour le compteur.

    Stratégie : IP du client. Pour les déploiements derrière un reverse
    proxy (Railway), slowapi lit `X-Forwarded-For` via `get_remote_address`.
    """
    return get_remote_address(request)


# Backend Redis si DSN dispo, sinon in-memory (utile pour les tests locaux
# où on n'a pas forcément Redis up).
_storage_uri = settings.REDIS_URL if getattr(settings, "REDIS_URL", None) else "memory://"

limiter = Limiter(
    key_func=_key_func,
    storage_uri=_storage_uri,
    # Headers `X-RateLimit-*` exposés au client pour debug
    headers_enabled=True,
    # Limite par défaut pour tous les endpoints non décorés explicitement —
    # garde-fou large (anti-DDOS basique).
    default_limits=["120/minute"],
)
