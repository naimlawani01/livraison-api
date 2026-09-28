"""Adresse IP réelle du client, impossible à falsifier.

Derrière Railway, la requête passe par le proxy d'entrée, qui AJOUTE l'IP qu'il
voit à la fin de l'en-tête `X-Forwarded-For`. Tout ce qui est à GAUCHE a pu être
écrit par le client lui-même (`X-Forwarded-For: 1.2.3.4`).

⚠️ Avant : uvicorn `--forwarded-allow-ips="*"` prenait la PREMIÈRE valeur, donc
celle choisie par l'attaquant → il contournait tous les rate-limits par IP
(brute-force du login, envoi massif de SMS OTP) en changeant l'en-tête à chaque
requête, et pouvait se faire passer pour 127.0.0.1 dans les logs.

Règle : on lit l'en-tête de DROITE à GAUCHE et on garde la première IP publique
(les sauts internes de Railway sont privés) — c'est celle vue par le proxy.
"""
import ipaddress
from typing import Optional


def _publique(valeur: str) -> Optional[str]:
    try:
        ip = ipaddress.ip_address(valeur.strip().split("%")[0])
    except ValueError:
        return None
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        return None
    return str(ip)


def ip_client(request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    for valeur in reversed(xff.split(",")):
        ip = _publique(valeur)
        if ip:
            return ip
    return request.client.host if request.client else "inconnue"
