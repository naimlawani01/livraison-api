import uuid
import logging
from typing import Optional
from ..core.config import settings

logger = logging.getLogger(__name__)


class StorageService:
    """Service d'upload vers Cloudflare R2 (compatible S3)"""

    def __init__(self):
        self._client = None
        self._initialize()

    def _initialize(self):
        if not all([settings.R2_ACCOUNT_ID, settings.R2_ACCESS_KEY_ID, settings.R2_SECRET_ACCESS_KEY]):
            logger.warning("Cloudflare R2 non configuré — uploads désactivés")
            return
        try:
            import boto3
            from botocore.config import Config
            self._client = boto3.client(
                "s3",
                endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
                aws_access_key_id=settings.R2_ACCESS_KEY_ID,
                aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
                config=Config(signature_version="s3v4"),
                region_name="auto",
            )
            logger.info("Cloudflare R2 initialisé")
        except Exception as e:
            logger.warning(f"R2 init échoué : {e}")

    @property
    def is_ready(self) -> bool:
        return self._client is not None

    async def upload_document(
        self,
        file_data: bytes,
        folder: str,
        original_filename: str,
        content_type: str = "image/jpeg",
    ) -> str:
        """
        Upload un fichier dans R2 et retourne l'URL publique.
        folder : ex. "livreurs" ou "expediteurs"
        """
        if not self._client:
            raise RuntimeError("R2 non configuré — ajoutez R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY dans les variables d'environnement")

        ext = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else "jpg"
        # Sanitize : garder uniquement alphanum, borner la longueur (défense en profondeur).
        ext = "".join(c for c in ext if c.isalnum())[:5] or "jpg"
        key = f"{folder}/{uuid.uuid4()}.{ext}"

        self._client.put_object(
            Bucket=settings.R2_BUCKET_NAME,
            Key=key,
            Body=file_data,
            ContentType=content_type,
        )

        public_url = settings.R2_PUBLIC_URL or f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com/{settings.R2_BUCKET_NAME}"
        return f"{public_url}/{key}"

    def presigned_url(self, key: str, expires_in: int = 3600) -> Optional[str]:
        """URL signée temporaire pour que l'admin puisse visualiser un document privé.

        Version synchrone — utilisée quand on n'a pas de contexte async ou
        que la perf n'est pas critique. Préférer `presigned_url_cached()`
        depuis les endpoints async (admin), qui évite de recalculer une
        URL pour la même clé pendant ~90% de son TTL.
        """
        if not self._client:
            return None
        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": settings.R2_BUCKET_NAME, "Key": key},
                ExpiresIn=expires_in,
            )
        except Exception:
            return None

    async def presigned_url_cached(
        self, key: str, expires_in: int = 3600
    ) -> Optional[str]:
        """Variante async de `presigned_url()` avec cache Redis.

        Évite de re-générer une URL identique à chaque clic admin dans
        ValidationPage / LivreursPage. Cache valable 90% du TTL pour avoir
        une marge de sécurité avant expiration.

        Si Redis est indisponible, fallback transparent sur `presigned_url()`.
        """
        if not self._client:
            return None

        cache_key = f"r2:url:{key}:exp{expires_in}"
        try:
            from ..core.redis import redis_client
            cached = await redis_client.get(cache_key)
            if cached:
                # `redis-py` async retourne str ou bytes selon la version
                return cached.decode() if isinstance(cached, bytes) else cached

            url = self.presigned_url(key, expires_in=expires_in)
            if url:
                # Cache 90% du TTL — assure que l'URL en cache reste valide
                # quand un client l'utilise juste après l'avoir lue.
                await redis_client.set(cache_key, url, ex=int(expires_in * 0.9))
            return url
        except Exception:
            # Redis down ou erreur sérialization → on génère sans cache
            return self.presigned_url(key, expires_in=expires_in)

    def _key_from_url(self, url: str) -> Optional[str]:
        """Extrait la clé R2 depuis une URL publique complète."""
        if not url or not settings.R2_PUBLIC_URL:
            return None
        prefix = settings.R2_PUBLIC_URL.rstrip("/") + "/"
        return url[len(prefix):] if url.startswith(prefix) else None

    def signed_view_url(self, stored_url: Optional[str], expires_in: int = 3600) -> Optional[str]:
        """URL de consultation **temporaire** pour un document stocké.

        Convertit l'URL publique enregistrée (`piece_identite_url`, `devanture_url`,
        …) en URL présignée à durée limitée → les pièces d'identité / documents
        sensibles ne sont plus servis via une URL publique permanente, mais via un
        lien signé, borné dans le temps, généré à la demande pour l'admin
        authentifié (le bucket R2 peut alors être privé).

        Synchrone : `generate_presigned_url` est une signature locale (pas d'appel
        réseau), utilisable directement dans les compréhensions des endpoints admin.
        Fallback gracieux sur l'URL d'origine si la clé n'est pas dérivable ou si
        R2 n'est pas configuré — l'admin voit toujours quelque chose.
        """
        if not stored_url:
            return None
        key = self._key_from_url(stored_url)
        if not key:
            return stored_url
        return self.presigned_url(key, expires_in=expires_in) or stored_url


storage_service = StorageService()
