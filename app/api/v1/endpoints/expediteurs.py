from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from ....core.database import get_db
from ....models.expediteur import Expediteur
from ....models.user import User
from ....schemas.expediteur import (
    ExpediteurCreate,
    ExpediteurUpdate,
    ExpediteurResponse
)
from ....utils.dependencies import get_current_user, get_current_expediteur
from ....services.storage_service import storage_service

router = APIRouter()


@router.post("/", response_model=ExpediteurResponse, status_code=status.HTTP_201_CREATED)
async def create_expediteur(
    expediteur_data: ExpediteurCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Créer le profil expediteur (après inscription)"""
    # Vérifier qu'il n'a pas déjà un profil
    query = select(Expediteur).where(Expediteur.user_id == current_user.id)
    result = await db.execute(query)
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Profil expediteur déjà existant"
        )
    
    # Créer le expediteur
    expediteur = Expediteur(
        user_id=current_user.id,
        type_expediteur=expediteur_data.type_expediteur,
        nom=expediteur_data.nom,
        description=expediteur_data.description,
        adresse=expediteur_data.adresse,
        latitude=expediteur_data.latitude,
        longitude=expediteur_data.longitude,
        email=expediteur_data.email,
        telephone_secondaire=expediteur_data.telephone_secondaire,
        horaires=expediteur_data.horaires
    )
    
    db.add(expediteur)
    await db.commit()
    await db.refresh(expediteur)
    
    return expediteur


@router.get("/me", response_model=ExpediteurResponse)
async def get_my_expediteur(
    expediteur: Expediteur = Depends(get_current_expediteur)
):
    """Obtenir mon profil expediteur"""
    return expediteur


@router.patch("/me", response_model=ExpediteurResponse)
async def update_my_expediteur(
    expediteur_data: ExpediteurUpdate,
    expediteur: Expediteur = Depends(get_current_expediteur),
    db: AsyncSession = Depends(get_db)
):
    """Mettre à jour mon profil expediteur"""
    update_dict = expediteur_data.model_dump(exclude_unset=True)
    
    for key, value in update_dict.items():
        setattr(expediteur, key, value)
    
    await db.commit()
    await db.refresh(expediteur)
    
    return expediteur


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_account(
    expediteur: Expediteur = Depends(get_current_expediteur),
    db: AsyncSession = Depends(get_db)
):
    """Supprimer mon compte (droit à l’effacement RGPD)"""
    # Anonymiser les données personnelles plutôt que supprimer
    # pour conserver l’historique des courses
    expediteur.nom = "Compte supprimé"
    expediteur.email = None
    expediteur.description = None
    expediteur.adresse = "Anonymisé"
    expediteur.latitude = 0.0
    expediteur.longitude = 0.0
    expediteur.telephone_secondaire = None
    expediteur.is_open = False

    # Anonymiser le user
    user_query = select(User).where(User.id == expediteur.user_id)
    result = await db.execute(user_query)
    user = result.scalar_one_or_none()
    if user:
        user.phone = f"deleted_{expediteur.id}"
        user.password_hash = "deleted"

    await db.commit()


@router.get("/", response_model=List[ExpediteurResponse])
async def list_expediteurs(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db)
):
    """Lister les expediteurs (pour admin ou public)"""
    query = select(Expediteur).offset(skip).limit(limit)
    result = await db.execute(query)
    expediteurs = result.scalars().all()
    
    return expediteurs


@router.get("/{expediteur_id}", response_model=ExpediteurResponse)
async def get_expediteur(
    expediteur_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Obtenir un expediteur par ID"""
    query = select(Expediteur).where(Expediteur.id == expediteur_id)
    result = await db.execute(query)
    expediteur = result.scalar_one_or_none()

    if not expediteur:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Expediteur non trouvé"
        )

    return expediteur


@router.post("/me/upload-document")
async def upload_document(
    file: UploadFile = File(...),
    document_type: str = Form("devanture"),  # "devanture" | "rccm"
    expediteur: Expediteur = Depends(get_current_expediteur),
    db: AsyncSession = Depends(get_db)
):
    """Uploader un document expediteur vers Cloudflare R2.

    document_type: "devanture" (photo de la devanture) ou "rccm" (registre du commerce).
    """
    if document_type not in ("devanture", "rccm"):
        raise HTTPException(status_code=400, detail="document_type doit être 'devanture' ou 'rccm'.")

    content_type = file.content_type or "application/octet-stream"
    allowed_types = {
        "image/jpeg", "image/jpg", "image/png", "image/heic", "image/heif", "image/webp",
        "application/pdf",
    }
    if content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Format non supporté. Utilisez une image (JPG, PNG, HEIC) ou un PDF.")

    # Plafond de taille (anti-DoS) — aligné sur settings.MAX_UPLOAD_BYTES (8 Mo).
    _max = 8 * 1024 * 1024
    if file.size is not None and file.size > _max:
        raise HTTPException(status_code=413, detail="Fichier trop volumineux (max 8 Mo).")

    content = await file.read()
    if len(content) > _max:
        raise HTTPException(status_code=413, detail="Fichier trop volumineux (max 8 Mo).")

    # Vérification des magic bytes — protège contre un fichier malveillant
    # renommé en .jpg/.pdf. Le content-type déclaré par le client est non fiable.
    import filetype as _ft
    _ALLOWED_MIME_REAL = {
        "image/jpeg", "image/png", "image/heic", "image/heif", "image/webp",
        "application/pdf",
    }
    detected = _ft.guess(content)
    if detected is None or detected.mime not in _ALLOWED_MIME_REAL:
        raise HTTPException(status_code=400, detail="Type de fichier non autorisé. Contenu invalide.")

    content_type = detected.mime  # utiliser le type réel, pas celui déclaré

    if document_type == "devanture" and content_type == "application/pdf":
        raise HTTPException(status_code=400, detail="La photo de devanture doit être une image (pas un PDF).")

    ext_default = "pdf" if content_type == "application/pdf" else "jpg"
    url = await storage_service.upload_document(
        file_data=content,
        folder="expediteurs",
        original_filename=file.filename or f"{document_type}.{ext_default}",
        content_type=content_type,
    )

    if document_type == "rccm":
        expediteur.rccm_url = url
    else:
        expediteur.devanture_url = url

    await db.commit()
    return {"message": "Document uploadé avec succès", "url": url, "document_type": document_type}

