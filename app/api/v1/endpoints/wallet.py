"""
Wallet du livreur — solde, historique des transactions, demande de retrait.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
import logging

from ....core.database import get_db
from ....models.livreur import Livreur
from ....models.wallet_transaction import WalletTransaction
from ....utils.dependencies import get_current_livreur

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Schémas ─────────────────────────────────────────────────────────────────

class WalletSummary(BaseModel):
    solde_disponible: float
    total_gains: float
    nombre_courses: int

class TransactionOut(BaseModel):
    id: str
    type: str
    montant: float
    solde_avant: float
    solde_apres: float
    description: Optional[str]
    statut: str
    created_at: str

class RetraitRequest(BaseModel):
    montant: float = Field(..., gt=0, description="Montant à retirer (> 0)")
    methode: str = Field(..., description="orange_money | mtn_money | wave")
    numero_telephone: str = Field(..., min_length=8, max_length=20)

class RechargeRequest(BaseModel):
    montant: float = Field(..., gt=0)
    methode: str = Field(..., description="orange_money | mtn_money | wave")
    numero_telephone: str = Field(..., min_length=8, max_length=20)
    reference_transaction: str = Field(..., min_length=3, description="Référence de la transaction Mobile Money")

# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/me/wallet", response_model=WalletSummary)
async def get_wallet(
    livreur: Livreur = Depends(get_current_livreur),
    db: AsyncSession = Depends(get_db),
):
    """Retourne le solde et les statistiques du wallet."""
    return WalletSummary(
        solde_disponible=round(livreur.solde_disponible, 2),
        total_gains=round(livreur.total_gains, 2),
        nombre_courses=livreur.nombre_courses_completees,
    )


@router.get("/me/wallet/transactions")
async def get_transactions(
    page: int = 1,
    limit: int = 20,
    livreur: Livreur = Depends(get_current_livreur),
    db: AsyncSession = Depends(get_db),
):
    """Historique paginé des transactions du livreur."""
    offset = (page - 1) * limit

    count_q = select(func.count()).where(WalletTransaction.livreur_id == livreur.id)
    count_r = await db.execute(count_q)
    total = count_r.scalar() or 0

    txn_q = (
        select(WalletTransaction)
        .where(WalletTransaction.livreur_id == livreur.id)
        .order_by(WalletTransaction.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    txn_r = await db.execute(txn_q)
    transactions = txn_r.scalars().all()

    return {
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit,
        "transactions": [
            {
                "id": str(t.id),
                "type": t.type,
                "montant": t.montant,
                "solde_avant": t.solde_avant,
                "solde_apres": t.solde_apres,
                "description": t.description,
                "statut": t.statut,
                "created_at": t.created_at.isoformat(),
            }
            for t in transactions
        ],
    }


@router.post("/me/wallet/recharge", status_code=status.HTTP_201_CREATED)
async def demander_recharge(
    body: RechargeRequest,
    livreur: Livreur = Depends(get_current_livreur),
    db: AsyncSession = Depends(get_db),
):
    """
    Recharge du wallet via Mobile Money.
    Le livreur soumet la référence de sa transaction — un admin valide ensuite.
    Le solde n'est crédité qu'après validation.
    """
    montant_min = 5_000
    if body.montant < montant_min:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Montant minimum de recharge : {montant_min} GNF",
        )

    txn = WalletTransaction(
        livreur_id=livreur.id,
        type="recharge",
        montant=body.montant,
        solde_avant=livreur.solde_disponible,
        solde_apres=livreur.solde_disponible,  # inchangé jusqu'à validation admin
        description=f"Recharge {body.methode} — réf. {body.reference_transaction} — {body.numero_telephone}",
        statut="en_attente",
    )
    db.add(txn)
    await db.commit()

    return {
        "message": "Demande de recharge enregistrée. En attente de validation.",
        "montant": body.montant,
        "transaction_id": str(txn.id),
        "statut": "en_attente",
    }


@router.post("/me/wallet/retrait", status_code=status.HTTP_201_CREATED)
async def demander_retrait(
    body: RetraitRequest,
    livreur: Livreur = Depends(get_current_livreur),
    db: AsyncSession = Depends(get_db),
):
    """Demande de retrait du solde vers Mobile Money."""
    montant_min = 5_000  # GNF minimum
    if body.montant < montant_min:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Montant minimum de retrait : {montant_min} GNF",
        )

    if body.montant > livreur.solde_disponible:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Solde insuffisant. Disponible : {livreur.solde_disponible} GNF",
        )

    solde_avant = livreur.solde_disponible
    livreur.solde_disponible -= body.montant

    txn = WalletTransaction(
        livreur_id=livreur.id,
        type="retrait",
        montant=body.montant,
        solde_avant=solde_avant,
        solde_apres=livreur.solde_disponible,
        description=f"Retrait {body.methode} → {body.numero_telephone}",
        statut="en_attente",
    )
    db.add(txn)
    await db.commit()
    await db.refresh(txn)

    # Tenter le payout automatique via GeniusPay
    from ....core.config import settings
    if settings.GENIUSPAY_API_KEY and settings.GENIUSPAY_WALLET_ID:
        try:
            from ....services import genius_pay_service
            # Récupérer le nom du livreur pour le payout
            from sqlalchemy import select as sa_select
            from ....models.user import User
            q = sa_select(User).where(User.id == livreur.user_id)
            r = await db.execute(q)
            user = r.scalar_one_or_none()
            nom_livreur = f"{user.prenom} {user.nom}" if user else "Livreur"

            payout = await genius_pay_service.initier_payout(
                livreur_id=str(livreur.id),
                montant=body.montant,
                telephone=body.numero_telephone,
                provider=body.methode,          # "orange_money" | "mtn_money" | "wave"
                nom_livreur=nom_livreur,
                idempotency_key=f"retrait-{txn.id}",
            )
            txn.geniuspay_reference = payout.get("reference")
            txn.statut = "en_cours"
            await db.commit()
            logger.info("Payout GeniusPay initié: ref=%s livreur=%s", payout.get("reference"), livreur.id)
        except Exception as e:
            logger.error("GeniusPay payout échoué: %s — retrait restera en_attente", e)
            # Ne pas rollback : l'admin pourra traiter manuellement

    return {
        "message": "Demande de retrait enregistrée"
                   + (" — paiement Mobile Money en cours" if txn.statut == "en_cours" else " — traitement en attente"),
        "montant": body.montant,
        "solde_apres": round(livreur.solde_disponible, 2),
        "transaction_id": str(txn.id),
        "statut": txn.statut,
    }
