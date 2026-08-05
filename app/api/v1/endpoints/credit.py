"""Crédit de l'Expéditeur (partenaire) — solde, historique, recharge (PSP).

Miroir côté expéditeur du wallet livreur (``wallet.py``), monté sous ``/partenaires``.
Le Crédit se **dépense** (commission des courses) et se **recharge** via Mobile Money :
la recharge est appliquée par le webhook GeniusPay après confirmation du paiement
(cf. ``payments.py``), pas ici.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

from ....core.database import get_db
from ....core.config import settings
from ....models.partenaire import Partenaire
from ....models.credit_transaction import CreditTransaction
from ....services import genius_pay_service, soldes
from ....services.genius_pay_service import GeniusPayError
from ....utils.dependencies import get_current_partenaire

logger = logging.getLogger(__name__)
router = APIRouter()


class RechargeCreditRequest(BaseModel):
    montant: float = Field(..., gt=0, description="Montant à recharger (GNF)")


@router.get("/me/credit")
async def get_credit(
    partenaire: Partenaire = Depends(get_current_partenaire),
    db: AsyncSession = Depends(get_db),
):
    """Solde de Crédit courant de l'expéditeur."""
    return {"credit_solde": round(partenaire.credit_solde or 0.0, 2)}


@router.get("/me/credit/transactions")
async def get_credit_transactions(
    page: int = 1,
    limit: int = 20,
    partenaire: Partenaire = Depends(get_current_partenaire),
    db: AsyncSession = Depends(get_db),
):
    """Historique paginé des mouvements de Crédit (recharge / commission / remboursement)."""
    offset = (page - 1) * limit

    count_r = await db.execute(
        select(func.count()).where(CreditTransaction.partenaire_id == partenaire.id)
    )
    total = count_r.scalar() or 0

    txn_r = await db.execute(
        select(CreditTransaction)
        .where(CreditTransaction.partenaire_id == partenaire.id)
        .order_by(CreditTransaction.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
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
                "commande_id": str(t.commande_id) if t.commande_id else None,
                "statut": t.statut,
                "created_at": t.created_at.isoformat(),
            }
            for t in transactions
        ],
    }


@router.post("/me/credit/recharge", status_code=status.HTTP_201_CREATED)
async def recharger_credit(
    body: RechargeCreditRequest,
    partenaire: Partenaire = Depends(get_current_partenaire),
    db: AsyncSession = Depends(get_db),
):
    """Initie une recharge du Crédit via Mobile Money.

    Retourne un ``checkout_url`` GeniusPay. Le Crédit n'est crédité qu'après
    confirmation du paiement par le webhook (``payment.success``).
    """
    if body.montant < soldes.MONTANT_MIN_RECHARGE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Recharge minimum : {soldes.MONTANT_MIN_RECHARGE} GNF",
        )
    if not settings.GENIUSPAY_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Paiement Mobile Money indisponible",
        )

    try:
        paiement = await genius_pay_service.initier_paiement(
            partenaire_id=str(partenaire.id),
            montant=body.montant,
            description="Recharge Crédit Sönaiyaa",
            metadata={"type": "credit_recharge", "montant": int(body.montant)},
        )
    except GeniusPayError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))

    return {
        "reference": paiement.get("reference"),
        "checkout_url": paiement.get("checkout_url"),
        "montant": body.montant,
    }
