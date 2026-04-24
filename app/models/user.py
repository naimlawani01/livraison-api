from sqlalchemy import Column, String, Boolean, DateTime, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime, timezone
import uuid
import enum
from ..core.database import Base


class UserRole(str, enum.Enum):
    """Rôles des utilisateurs"""
    ADMIN = "ADMIN"
    PARTENAIRE = "PARTENAIRE"
    LIVREUR = "LIVREUR"


class User(Base):
    """Modèle de base pour tous les utilisateurs"""
    __tablename__ = "users"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    phone = Column(String(20), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=True)  # Optionnel si OTP uniquement
    role = Column(SQLEnum(UserRole), nullable=False)
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    last_login = Column(DateTime(timezone=True), nullable=True)
    
    # OTP
    otp_code = Column(String(6), nullable=True)
    otp_expires_at = Column(DateTime(timezone=True), nullable=True)
    
    # Push notifications (FCM)
    device_token = Column(String(500), nullable=True)
    
    def __repr__(self):
        return f"<User {self.phone} ({self.role})>"
