"""Service transactionnel du Crédit (Expéditeur).

Emballage **mince** autour des règles pures de ``app/services/soldes.py``, avec
verrou de ligne pessimiste (``with_for_update``) pour empêcher deux requêtes
concurrentes de corrompre le solde — même pattern éprouvé que le retrait livreur
(``endpoints/wallet.py``).

Toutes les **décisions** (peut-on débiter ? recharger ? plancher ?) vivent dans
``soldes.py`` et y sont testées. Ici : uniquement la persistance.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.expediteur import Expediteur
from ..models.credit_transaction import CreditTransaction
from . import soldes

logger = logging.getLogger(__name__)


class ExpediteurIntrouvable(ValueError):
    """Aucun expéditeur (expediteur) pour cet identifiant."""


async def _lock_expediteur(db: AsyncSession, expediteur_id) -> Expediteur:
    """Charge et verrouille la ligne expediteur pour un débit/recharge atomique."""
    q = select(Expediteur).where(Expediteur.id == expediteur_id).with_for_update()
    r = await db.execute(q)
    p = r.scalar_one_or_none()
    if p is None:
        raise ExpediteurIntrouvable(f"Expéditeur {expediteur_id} introuvable")
    return p


async def credit_disponible(db: AsyncSession, expediteur_id) -> float:
    """Solde de Crédit courant (lecture simple, sans verrou)."""
    q = select(Expediteur.credit_solde).where(Expediteur.id == expediteur_id)
    r = await db.execute(q)
    solde = r.scalar_one_or_none()
    return float(solde or 0.0)


async def couvre_commission(db: AsyncSession, expediteur_id, commission: float) -> bool:
    """Garde-fou de création de course : le Crédit couvre-t-il la commission ?"""
    return soldes.credit_couvre(await credit_disponible(db, expediteur_id), commission)


async def recharger(
    db: AsyncSession,
    expediteur_id,
    montant: float,
    *,
    description: Optional[str] = None,
    geniuspay_reference: Optional[str] = None,
) -> CreditTransaction:
    """Applique une recharge **confirmée** du Crédit (à appeler après paiement PSP).

    Lève ``MontantInvalide`` (soldes) si le montant est sous le plancher.
    """
    p = await _lock_expediteur(db, expediteur_id)
    avant = p.credit_solde or 0.0
    apres = soldes.credit_recharger(avant, montant)  # valide plancher + positif
    p.credit_solde = apres

    txn = CreditTransaction(
        expediteur_id=p.id,
        type="recharge",
        montant=montant,
        solde_avant=avant,
        solde_apres=apres,
        description=description,
        statut="complete",
        geniuspay_reference=geniuspay_reference,
    )
    db.add(txn)
    await db.commit()
    await db.refresh(txn)
    return txn


async def debiter_commission(
    db: AsyncSession,
    expediteur_id,
    commission: float,
    *,
    commande_id=None,
    description: Optional[str] = None,
) -> CreditTransaction:
    """Débite la commission d'une course du Crédit de l'expéditeur.

    Lève ``SoldeInsuffisant`` (soldes) si le Crédit ne couvre pas — dans ce cas
    la course ne doit pas être créée/diffusée.
    """
    p = await _lock_expediteur(db, expediteur_id)
    avant = p.credit_solde or 0.0
    apres = soldes.credit_debiter(avant, commission)  # lève si insuffisant
    p.credit_solde = apres

    txn = CreditTransaction(
        expediteur_id=p.id,
        type="commission",
        montant=commission,
        solde_avant=avant,
        solde_apres=apres,
        commande_id=commande_id,
        description=description,
        statut="complete",
    )
    db.add(txn)
    await db.commit()
    await db.refresh(txn)
    return txn


async def rembourser_commission(
    db: AsyncSession,
    expediteur_id,
    montant: float,
    *,
    commande_id=None,
    description: Optional[str] = None,
) -> CreditTransaction:
    """Recrédite une commission au Crédit (course annulée). Le pendant de debiter_commission."""
    p = await _lock_expediteur(db, expediteur_id)
    avant = p.credit_solde or 0.0
    apres = soldes.gains_crediter(avant, montant)  # simple ajout, valide positif
    p.credit_solde = apres

    txn = CreditTransaction(
        expediteur_id=p.id,
        type="remboursement",
        montant=montant,
        solde_avant=avant,
        solde_apres=apres,
        commande_id=commande_id,
        description=description,
        statut="complete",
    )
    db.add(txn)
    await db.commit()
    await db.refresh(txn)
    return txn


async def ajuster_commission(
    db: AsyncSession,
    expediteur_id,
    ancienne: float,
    nouvelle: float,
    *,
    commande_id=None,
) -> None:
    """Ajuste le Crédit du delta quand le prix (donc la commission) est recalculé.

    Best-effort et non bloquant (appelé depuis le flux public de partage GPS) :
    ne fait jamais passer le Crédit sous zéro. Un éventuel manque est journalisé,
    pas remonté en erreur — le chemin provisoire/GPS est en voie de dépréciation
    au profit du prix ferme par quartier.
    """
    delta = round(nouvelle - ancienne, 2)
    if delta == 0:
        return

    p = await _lock_expediteur(db, expediteur_id)
    avant = p.credit_solde or 0.0

    if delta > 0:
        montant = min(delta, avant)  # cap : jamais sous zéro
        apres = round(avant - montant, 2)
        type_ = "commission"
        if montant < delta:
            logger.warning(
                "Crédit insuffisant pour ajuster commission course=%s : manque=%s",
                commande_id, round(delta - montant, 2),
            )
    else:
        montant = -delta
        apres = round(avant + montant, 2)
        type_ = "remboursement"

    if montant <= 0:
        return

    p.credit_solde = apres
    txn = CreditTransaction(
        expediteur_id=p.id,
        type=type_,
        montant=montant,
        solde_avant=avant,
        solde_apres=apres,
        commande_id=commande_id,
        description="Ajustement commission (recalcul prix)",
        statut="complete",
    )
    db.add(txn)
    await db.commit()


async def crediter_admin(
    db: AsyncSession,
    expediteur_id,
    montant: float,
    *,
    motif: Optional[str] = None,
) -> CreditTransaction:
    """Crédit manuel par un administrateur (correction, geste commercial,
    confirmation d'un paiement Mobile Money hors PSP).

    Contrairement à ``recharger``, pas de plancher : l'admin ajuste librement
    (montant > 0). Tracé dans le journal sous le type ``ajustement_admin``.
    """
    if montant <= 0:
        raise soldes.MontantInvalide("Le montant doit être strictement positif.")
    p = await _lock_expediteur(db, expediteur_id)
    avant = p.credit_solde or 0.0
    apres = round(avant + montant, 2)
    p.credit_solde = apres

    txn = CreditTransaction(
        expediteur_id=p.id,
        type="ajustement_admin",
        montant=montant,
        solde_avant=avant,
        solde_apres=apres,
        description=f"Crédit manuel admin — {motif}" if motif else "Crédit manuel admin",
        statut="complete",
    )
    db.add(txn)
    await db.commit()
    await db.refresh(txn)
    return txn
