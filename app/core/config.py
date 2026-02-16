from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Optional


class Settings(BaseSettings):
    """Configuration de l'application"""
    
    # Application
    APP_NAME: str = "Plateforme Livraison"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    API_PREFIX: str = "/api/v1"
    
    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    
    # Database
    DATABASE_URL: str
    DATABASE_TEST_URL: Optional[str] = None
    
    # Security
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    
    # SMS / OTP
    TWILIO_ACCOUNT_SID: Optional[str] = None
    TWILIO_AUTH_TOKEN: Optional[str] = None
    TWILIO_PHONE_NUMBER: Optional[str] = None
    
    # Firebase
    FIREBASE_CREDENTIALS: Optional[str] = None
    
    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    
    # Geolocation
    DEFAULT_SEARCH_RADIUS_KM: float = 5.0
    MAX_SEARCH_RADIUS_KM: float = 10.0
    MIN_SEARCH_RADIUS_KM: float = 1.0
    
    # Courses
    MAX_COURSES_SIMULTANEES: int = 2  # Nombre max de courses en parallèle par livreur
    
    # Commission
    PLATFORM_COMMISSION_PERCENTAGE: float = 15.0
    
    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:8080"]
    
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True
    )


settings = Settings()
