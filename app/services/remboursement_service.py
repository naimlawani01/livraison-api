"""Remboursement d'une course Mobile Money payée puis annulée.

* Payeur ``expediteur`` : ce qu'il a payé lui revient en **avoir sur son Crédit**
  (automatique, l'argent reste utilisable pour ses prochaines courses).
* Payeur ``client`` : le montant est enregistré dans ``Course.remboursement_du``
  et traité à la main par l'admin (``/admin/remboursements``) tant que le PSP
  n'offre pas de remboursement par API.

Idempotent : peut être appelé à l'annulation ET au webhook de paiement tardif.
"""
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.course import Course, ModePaiement, Payeur
from . import credit_service

logger = logging.getLogger(__name__)


async def enregistrer_remboursement(db: AsyncSession, course: Course) -> None:
    if course.mode_paiement != ModePaiement.MOBILE_MONEY or course.paiement_confirme != "oui":
        return
    montant = course.montant_a_encaisser

    if course.payeur == Payeur.CLIENT.value:
        if course.remboursement_du:
            return
        course.remboursement_du = montant
        await db.commit()
        logger.warning(
            "Remboursement client à effectuer",
            extra={"course_id": str(course.id), "montant": montant},
        )
    else:
        await credit_service.crediter_avoir(
            db, course.expediteur_id, montant,
            course_id=course.id,
            description=f"Avoir — course annulée #{course.numero_course}",
        )
