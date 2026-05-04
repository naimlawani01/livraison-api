from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta, timezone
from ....core.database import get_db
from ....core.security import (
    verify_password,
    get_password_hash,
    create_access_token,
    create_refresh_token,
    generate_otp
)
from ....models.user import User
from ....models.livreur import Livreur
from ....schemas.user import (
    UserCreate,
    UserLogin,
    OTPRequest,
    OTPVerify,
    TokenResponse,
    UserResponse,
    DeviceTokenUpdate
)
from ....services.sms_service import sms_service
from ....utils.dependencies import get_current_user
from ....core.redis import redis_client

router = APIRouter()

OTP_RATE_LIMIT = 3   # tentatives max
OTP_RATE_WINDOW = 300  # 5 minutes


async def _check_otp_rate_limit(phone: str) -> None:
    """Bloque si trop de tentatives OTP pour ce numéro sur la fenêtre glissante."""
    key = f"otp_rl:{phone}"
    try:
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, OTP_RATE_WINDOW)
        if count > OTP_RATE_LIMIT:
            ttl = await redis_client.ttl(key)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Trop de tentatives. Réessayez dans {ttl} secondes.",
            )
    except HTTPException:
        raise
    except Exception:
        pass  # Redis indisponible → on laisse passer plutôt que bloquer


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db)
):
    """Inscription d'un nouvel utilisateur"""
    # Vérifier si le numéro existe déjà
    query = select(User).where(User.phone == user_data.phone)
    result = await db.execute(query)
    existing_user = result.scalar_one_or_none()
    
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ce numéro de téléphone est déjà enregistré"
        )
    
    # Créer l'utilisateur
    user = User(
        phone=user_data.phone,
        role=user_data.role,
        password_hash=get_password_hash(user_data.password) if user_data.password else None
    )
    
    # Générer et envoyer OTP
    otp_code = generate_otp()
    user.otp_code = otp_code
    user.otp_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    
    # En mode DEBUG, auto-vérifier le compte (pas de vrai SMS)
    from ....core.config import settings
    if settings.DEBUG:
        user.is_verified = True
        user.otp_code = None
        user.otp_expires_at = None
    
    db.add(user)
    await db.commit()
    await db.refresh(user)
    
    # Générer les tokens (immédiat après inscription pour fluidifier l'UX)
    access_token = create_access_token({"sub": str(user.id), "role": user.role})
    refresh_token = create_refresh_token({"sub": str(user.id)})
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserResponse.model_validate(user)
    )


@router.post("/request-otp")
async def request_otp(
    otp_request: OTPRequest,
    db: AsyncSession = Depends(get_db)
):
    """Demander un code OTP"""
    await _check_otp_rate_limit(otp_request.phone)

    query = select(User).where(User.phone == otp_request.phone)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur non trouvé"
        )

    otp_code = generate_otp()
    user.otp_code = otp_code
    user.otp_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)

    await db.commit()
    await sms_service.envoyer_otp(user.phone, otp_code)

    return {"message": "Code OTP envoyé"}


@router.post("/verify-otp", response_model=TokenResponse)
async def verify_otp(
    otp_verify: OTPVerify,
    db: AsyncSession = Depends(get_db)
):
    """Vérifier le code OTP et connecter l'utilisateur"""
    await _check_otp_rate_limit(otp_verify.phone)

    query = select(User).where(User.phone == otp_verify.phone)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur non trouvé"
        )

    if user.otp_code != otp_verify.otp_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Code OTP invalide"
        )
    
    # Vérifier l'expiration
    if user.otp_expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Code OTP expiré"
        )
    
    # Marquer comme vérifié
    user.is_verified = True
    user.otp_code = None
    user.otp_expires_at = None
    user.last_login = datetime.now(timezone.utc)
    
    await db.commit()
    await db.refresh(user)
    
    # Générer les tokens
    access_token = create_access_token({"sub": str(user.id), "role": user.role})
    refresh_token = create_refresh_token({"sub": str(user.id)})
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserResponse.model_validate(user)
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    login_data: UserLogin,
    db: AsyncSession = Depends(get_db)
):
    """Connexion avec téléphone et mot de passe"""
    query = select(User).where(User.phone == login_data.phone)
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identifiants incorrects"
        )
    
    # Vérifier le mot de passe si fourni
    if login_data.password:
        if not user.password_hash or not verify_password(login_data.password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Identifiants incorrects"
            )
    
    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compte non vérifié. Veuillez vérifier votre code OTP"
        )
    
    # Mettre à jour la dernière connexion
    user.last_login = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)
    
    # Générer les tokens
    access_token = create_access_token({"sub": str(user.id), "role": user.role})
    refresh_token = create_refresh_token({"sub": str(user.id)})
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserResponse.model_validate(user)
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_access_token(
    body: dict,
    db: AsyncSession = Depends(get_db)
):
    """Rafraîchir le token d'accès à partir d'un refresh token valide"""
    from ....core.security import decode_token

    refresh_token = body.get("refresh_token")
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="refresh_token requis"
        )

    try:
        payload = decode_token(refresh_token)
    except HTTPException:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token invalide ou expiré, veuillez vous reconnecter"
        )

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide (type incorrect)"
        )

    user_id = payload.get("sub")
    query = select(User).where(User.id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Utilisateur introuvable ou désactivé"
        )

    new_access_token = create_access_token({"sub": str(user.id), "role": user.role})
    new_refresh_token = create_refresh_token({"sub": str(user.id)})

    return TokenResponse(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
        user=UserResponse.model_validate(user)
    )


@router.post("/device-token")
async def update_device_token(
    token_data: DeviceTokenUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Enregistrer/mettre à jour le device token pour les notifications push"""
    current_user.device_token = token_data.device_token

    # Propager aussi sur le profil Livreur si l'utilisateur en est un
    result = await db.execute(select(Livreur).where(Livreur.user_id == current_user.id))
    livreur = result.scalar_one_or_none()
    if livreur:
        livreur.device_token = token_data.device_token

    await db.commit()
    return {"message": "Device token enregistré"}
