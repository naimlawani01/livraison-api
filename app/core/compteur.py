"""Compteurs à expiration (anti brute-force) résistants à une panne Redis.

Redis est la source normale (partagée entre workers). S'il est indisponible, on
bascule sur un compteur **en mémoire du worker** plutôt que de faire planter la
requête (avant : un livreur ne pouvait plus terminer de course si Redis tombait)
ou de désactiver la protection. Moins strict (un compteur par worker), mais la
limite reste appliquée pendant la panne.
"""
import logging
import time
from typing import Dict, Tuple

from .redis import redis_client

logger = logging.getLogger(__name__)

_local: Dict[str, Tuple[int, float]] = {}  # clé → (valeur, expire_à)


def _local_get(cle: str) -> int:
    valeur, expire = _local.get(cle, (0, 0.0))
    if expire and expire < time.monotonic():
        _local.pop(cle, None)
        return 0
    return valeur


async def lire(cle: str) -> int:
    try:
        return int(await redis_client.get(cle) or 0)
    except Exception:  # noqa: BLE001
        logger.warning("Redis indisponible — compteur local", extra={"cle": cle})
        return _local_get(cle)


async def incrementer(cle: str, ttl_secondes: int) -> int:
    try:
        valeur = await redis_client.incr(cle)
        await redis_client.expire(cle, ttl_secondes)
        return int(valeur)
    except Exception:  # noqa: BLE001
        valeur = _local_get(cle) + 1
        _local[cle] = (valeur, time.monotonic() + ttl_secondes)
        return valeur


async def effacer(cle: str) -> None:
    _local.pop(cle, None)
    try:
        await redis_client.delete(cle)
    except Exception:  # noqa: BLE001
        pass
