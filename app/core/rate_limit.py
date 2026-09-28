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
    """Identifie un client pour le compteur : son IP.

    `get_remote_address` lit `request.client.host`. Derrière le proxy Railway,
    ce champ ne contient la VRAIE IP client que si uvicorn tourne avec
    `--proxy-headers --forwarded-allow-ips=*` (cf. start.sh) — sinon c'est l'IP
    du proxy et le rate-limit devient global. Ce flag est activé au démarrage.

    Requête authentifiée (JWT valide) → compteur **par utilisateur** : en Guinée,
    beaucoup d'abonnés mobiles partagent la même IP publique (CGNAT opérateur) ;
    compter par IP bloquerait des dizaines d'utilisateurs légitimes d'un coup.
    Le JWT est vérifié (signature) : impossible de forger une clé.
    """
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            from jose import jwt
            payload = jwt.decode(auth[7:], settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            if payload.get("sub"):
                return f"user:{payload['sub']}"
        except Exception:  # noqa: BLE001 — token invalide/expiré : repli sur l'IP
            pass
    return get_remote_address(request)


# Backend Redis si DSN dispo, sinon in-memory (utile pour les tests locaux
# où on n'a pas forcément Redis up).
_storage_uri = settings.REDIS_URL if getattr(settings, "REDIS_URL", None) else "memory://"

limiter = Limiter(
    key_func=_key_func,
    storage_uri=_storage_uri,
    # ⚠️ headers_enabled=False — quand True, slowapi tente d'injecter les
    # headers `X-RateLimit-*` dans la réponse mais ça crash pour les
    # endpoints async retournant un modèle Pydantic (Response pas encore
    # construite au moment de l'injection). Bug connu de slowapi 0.1.x.
    headers_enabled=False,
    # Redis en panne → on ne fait pas planter login / OTP / partage de position :
    # slowapi bascule sur un compteur en mémoire (par worker) au lieu d'une 500.
    swallow_errors=True,
    in_memory_fallback_enabled=True,
    # Garde-fou global (120 req/min par IP) appliqué par SlowAPIMiddleware à
    # toutes les routes HTTP : freine l'aspiration de données et le flood. Les
    # routes à fort trafic légitime sont exemptées avec @limiter.exempt
    # (webhook PSP, health checks). Les WebSockets ne sont pas concernés.
    default_limits=["120/minute"],
)
