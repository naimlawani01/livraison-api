from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Optional


class Settings(BaseSettings):
    """Configuration de l'application"""
    
    # Application
    APP_NAME: str = "Plateforme Livraison"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    API_PREFIX: str = "/api/v1"

    # Verbosité — séparé de DEBUG pour ne pas noyer les logs en dev
    SQLALCHEMY_ECHO: bool = False  # SQLALCHEMY_ECHO=true pour debug SQL ponctuel
    
    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # URL publique du backend — pour construire les liens envoyés par SMS
    # (tracking, paiement, partage de position). Override via env en prod.
    PUBLIC_BASE_URL: str = "https://ample-mindfulness-production.up.railway.app"
    
    # Database
    DATABASE_URL: str
    DATABASE_TEST_URL: Optional[str] = None
    
    # Security
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480   # 8h
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    
    # SMS — PasseInfo (provider local Guinée)
    PASSEINFO_API_KEY: Optional[str] = None
    PASSEINFO_CLIENT_ID: Optional[str] = None
    
    # Firebase
    FIREBASE_CREDENTIALS: Optional[str] = None
    FIREBASE_CREDENTIALS_PATH: Optional[str] = None

    # Cloudflare R2 (stockage documents)
    R2_ACCOUNT_ID: Optional[str] = None
    R2_ACCESS_KEY_ID: Optional[str] = None
    R2_SECRET_ACCESS_KEY: Optional[str] = None
    R2_BUCKET_NAME: str = "sonaiyaa-documents"
    R2_PUBLIC_URL: Optional[str] = None  # ex: https://pub-xxx.r2.dev
    
    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    
    # GeniusPay
    GENIUSPAY_API_KEY: str = ""          # YOUR_MERCHANT_API_KEY
    GENIUSPAY_API_SECRET: str = ""       # sk_sandbox_xxx  /  sk_live_xxx
    GENIUSPAY_WEBHOOK_SECRET: str = ""   # whsec_xxx
    GENIUSPAY_BASE_URL: str = "https://pay.genius.ci/api/v1/merchant"
    GENIUSPAY_WALLET_ID: str = ""        # UUID du wallet payout Sönaiya
    
    # Geolocation
    DEFAULT_SEARCH_RADIUS_KM: float = 5.0
    MAX_SEARCH_RADIUS_KM: float = 10.0
    MIN_SEARCH_RADIUS_KM: float = 1.0
    
    # Courses
    MAX_COURSES_SIMULTANEES: int = 2  # Nombre max de courses en parallèle par livreur
    
    # Commission
    PLATFORM_COMMISSION_PERCENTAGE: float = 15.0
    
    # CORS
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8080",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ]
    CORS_ALLOW_ALL_ORIGINS: bool = False
    
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True
    )


settings = Settings()
