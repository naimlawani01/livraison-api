import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import HTTPException, status
from .config import settings

# Configuration du hachage de mot de passe
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Vérifier le mot de passe"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hasher le mot de passe"""
    return pwd_context.hash(password)


def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Créer un token d'accès JWT"""
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire, "type": "access", "jti": str(uuid.uuid4())})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

    return encoded_jwt


def create_refresh_token(data: Dict[str, Any]) -> str:
    """Créer un token de rafraîchissement"""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh", "jti": str(uuid.uuid4())})

    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def decode_token(token: str) -> Dict[str, Any]:
    """Décoder un token JWT"""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide ou expiré",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def blacklist_token(jti: str, ttl_seconds: int) -> None:
    """Invalider un token JWT en stockant son jti dans Redis."""
    if ttl_seconds <= 0:
        return
    from .redis import redis_client
    await redis_client.set(f"token_blacklist:{jti}", "1", ex=ttl_seconds)


async def is_token_blacklisted(jti: str) -> bool:
    """Retourne True si le jti est dans la blacklist Redis."""
    from .redis import redis_client
    return await redis_client.exists(f"token_blacklist:{jti}") > 0


def generate_otp() -> str:
    """Générer un code OTP à 6 chiffres avec un générateur cryptographiquement sûr.

    Utilise `secrets` plutôt que `random` (ce dernier est prédictible si
    on connaît le seed — risque de brute-force pour bypass OTP).
    """
    return str(secrets.randbelow(900_000) + 100_000)


def generate_delivery_code() -> str:
    """Générer un code PIN de livraison à 4 chiffres (crypto-safe).

    Utilisé pour confirmer la livraison auprès du client. Doit être
    imprévisible pour qu'un attaquant ne puisse pas usurper la livraison.
    """
    return str(secrets.randbelow(9_000) + 1_000)
