"""Réconciliation avec le PSP quand un webhook n'arrive pas.

Les webhooks peuvent être perdus (panne réseau, PSP, redéploiement). Plutôt que
de laisser un paiement « non confirmé » ou un retrait « en cours » pour
toujours, on interroge périodiquement le PSP et on applique les mêmes
transitions que le webhook (``paiement_service``).
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.wallet_transaction import WalletTransaction
from . import genius_pay_service, paiement_service

logger = logging.getLogger(__name__)

# Statuts PSP normalisés (les libellés exacts varient selon l'API / la version).
_PAYE = {"success", "succeeded", "successful", "completed", "complete", "paid"}
_ECHEC = {"failed", "failure", "cancelled", "canceled", "expired", "rejected", "refused"}

# On ne réconcilie un retrait qu'après ce délai (le webhook a sa chance d'abord).
DELAI_RETRAIT = timedelta(minutes=15)


def _normaliser(statut: Optional[str]) -> str:
    s = (statut or "").strip().lower()
    if s in _PAYE:
        return "paye"
    if s in _ECHEC:
        return "echec"
    return "en_attente"


async def statut_paiement(reference: str) -> str:
    """``paye`` | ``echec`` | ``en_attente`` | ``inconnu`` (PSP injoignable)."""
    try:
        data = await genius_pay_service.get_paiement(reference)
    except Exception as e:  # noqa: BLE001
        logger.warning("PSP injoignable (statut paiement)", extra={"reference": reference, "erreur": str(e)})
        return "inconnu"
    return _normaliser(data.get("status") or data.get("statut"))


async def reconcilier_retraits(db: AsyncSession, maintenant: Optional[datetime] = None) -> int:
    """Finalise les retraits livreur restés « en cours » (webhook cashout perdu).
    Retourne le nombre de retraits finalisés."""
    maintenant = maintenant or datetime.now(timezone.utc)
    txns = (await db.execute(
        select(WalletTransaction).where(
            WalletTransaction.statut == "en_cours",
            WalletTransaction.geniuspay_reference.is_not(None),
            WalletTransaction.created_at < maintenant - DELAI_RETRAIT,
        )
    )).scalars().all()

    n = 0
    for txn in txns:
        try:
            data = await genius_pay_service.get_payout(txn.geniuspay_reference)
        except Exception as e:  # noqa: BLE001
            logger.warning("PSP injoignable (statut retrait)",
                           extra={"reference": txn.geniuspay_reference, "erreur": str(e)})
            continue
        etat = _normaliser(data.get("status"))
        if etat == "en_attente":
            continue
        if await paiement_service.finaliser_retrait(db, txn.geniuspay_reference, succes=(etat == "paye")):
            n += 1
    return n
