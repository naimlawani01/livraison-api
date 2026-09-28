"""Transitions d'argent partagées entre le webhook PSP et les tâches de fond.

Le webhook peut être perdu (panne réseau, PSP) : la réconciliation périodique
(``reconciliation_service``) interroge alors le PSP et applique **exactement**
les mêmes transitions via ces fonctions. Toutes sont idempotentes.
"""
import logging
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.course import Course, CourseStatus, Payeur
from ..models.expediteur import Expediteur
from ..models.livreur import Livreur
from ..models.wallet_transaction import WalletTransaction
from . import credit_service, remboursement_service, soldes

logger = logging.getLogger(__name__)


async def confirmer_paiement_course(db: AsyncSession, course: Course, reference: Optional[str]) -> None:
    """Paiement Mobile Money d'une course reçu (webhook ou réconciliation)."""
    from .matching_service import MatchingService

    if course.paiement_confirme == "oui":
        return

    course.paiement_confirme = "oui"
    course.geniuspay_reference = reference or course.geniuspay_reference
    await db.commit()

    # Payé après annulation : rien à diffuser, on trace le remboursement.
    if course.status == CourseStatus.ANNULEE:
        await remboursement_service.enregistrer_remboursement(db, course)
        logger.warning("Paiement reçu sur une course annulée — remboursement tracé",
                       extra={"course_id": str(course.id)})
        return

    # Le client a payé le prix complet → commission rendue à l'expéditeur.
    if course.payeur == Payeur.CLIENT.value:
        try:
            await credit_service.restituer_commission(
                db, course.expediteur_id, course.id,
                description=f"Commission couverte par le client — course #{course.numero_course}",
            )
        except Exception as e:  # noqa: BLE001
            logger.error("Restitution Crédit échouée", extra={"course_id": str(course.id), "erreur": str(e)})

    expediteur = (await db.execute(
        select(Expediteur).where(Expediteur.id == course.expediteur_id)
    )).scalar_one_or_none()
    if expediteur:
        await MatchingService.diffuser_course(
            db, course, expediteur.latitude, expediteur.longitude, expediteur_nom=expediteur.nom,
        )


async def annuler_course_systeme(
    db: AsyncSession,
    course_id,
    raison: str,
    *,
    statuts: Iterable[CourseStatus] = (CourseStatus.CREEE, CourseStatus.DIFFUSEE),
) -> Optional[Course]:
    """Annulation décidée par le système (expiration, lien de paiement expiré).

    UPDATE conditionnel sur le statut → atomique face à une acceptation
    simultanée. Rend la commission et trace un éventuel remboursement MM.
    Retourne la course annulée, ou None si elle n'était plus annulable.
    """
    res = await db.execute(
        update(Course)
        .where(Course.id == course_id, Course.status.in_(tuple(statuts)))
        .values(status=CourseStatus.ANNULEE, annulee_at=datetime.now(timezone.utc), raison_annulation=raison)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    if res.rowcount != 1:
        return None
    course = (await db.execute(
        select(Course).where(Course.id == course_id).execution_options(populate_existing=True)
    )).scalar_one()
    try:
        await credit_service.restituer_commission(
            db, course.expediteur_id, course.id,
            description=f"Remboursement course annulée #{course.numero_course} ({raison})",
        )
        await remboursement_service.enregistrer_remboursement(db, course)
    except Exception as e:  # noqa: BLE001
        logger.error("Annulation système — restitution échouée", extra={"course_id": str(course_id), "erreur": str(e)})
    return course


async def finaliser_retrait(db: AsyncSession, reference: str, succes: bool) -> bool:
    """Retrait livreur terminé (succès) ou échoué (Gains recrédités).

    Verrou sur la transaction ET sur le livreur : un webhook et la
    réconciliation simultanés ne peuvent pas recréditer deux fois.
    Retourne True si la transaction a changé d'état.
    """
    txn = (await db.execute(
        select(WalletTransaction)
        .where(WalletTransaction.geniuspay_reference == reference)
        .with_for_update()
    )).scalar_one_or_none()
    if txn is None or txn.statut not in ("en_attente", "en_cours"):
        await db.commit()
        return False

    if succes:
        txn.statut = "complete"
    else:
        livreur = (await db.execute(
            select(Livreur).where(Livreur.id == txn.livreur_id)
            .with_for_update().execution_options(populate_existing=True)
        )).scalar_one_or_none()
        if livreur:
            livreur.solde_disponible = soldes.gains_crediter(livreur.solde_disponible or 0.0, txn.montant)
        txn.statut = "refuse"
    await db.commit()
    logger.info("Retrait finalisé", extra={"reference": reference, "succes": succes})
    return True
