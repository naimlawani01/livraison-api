"""Expiration automatique des courses.

Une course encore ``CREEE`` ou ``DIFFUSEE`` (aucun livreur ne l'a acceptée, ou
le client n'a jamais payé / partagé sa position) après
``settings.COURSE_EXPIRATION_MINUTES`` est annulée : la commission réservée est
rendue au Crédit de l'expéditeur, et un paiement Mobile Money éventuel est
remboursé (avoir ou remboursement client tracé).

⚠️ Webhook perdu : avant d'expirer une course Mobile Money non confirmée qui a
un lien de paiement, on **demande au PSP** si elle a été payée. Si oui, on la
confirme au lieu de l'annuler ; si le PSP ne répond pas, on la laisse pour le
passage suivant (on n'annule jamais une course peut-être payée).

Lancé toutes les 5 min par une tâche de fond (``main.py``), avec un verrou Redis
pour qu'un seul worker uvicorn le fasse à la fois.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..models.course import Course, CourseStatus, ModePaiement
from . import paiement_service, reconciliation_service

logger = logging.getLogger(__name__)

STATUTS_EXPIRABLES = (CourseStatus.CREEE, CourseStatus.DIFFUSEE)
RAISON = "Expirée : aucun livreur ou paiement dans le délai"


async def expirer_courses(db: AsyncSession, maintenant: Optional[datetime] = None) -> int:
    """Annule les courses en attente depuis trop longtemps. Retourne leur nombre."""
    maintenant = maintenant or datetime.now(timezone.utc)
    limite = maintenant - timedelta(minutes=settings.COURSE_EXPIRATION_MINUTES)
    courses = (await db.execute(
        select(Course).where(Course.status.in_(STATUTS_EXPIRABLES), Course.created_at < limite)
    )).scalars().all()

    n = 0
    for course in courses:
        if (
            course.mode_paiement == ModePaiement.MOBILE_MONEY
            and course.paiement_confirme != "oui"
            and course.geniuspay_reference
        ):
            etat = await reconciliation_service.statut_paiement(course.geniuspay_reference)
            if etat == "paye":
                await paiement_service.confirmer_paiement_course(db, course, course.geniuspay_reference)
                logger.warning("Paiement retrouvé auprès du PSP (webhook perdu)",
                               extra={"course_id": str(course.id)})
                continue
            if etat == "inconnu":
                continue  # PSP injoignable : on retentera au prochain passage

        annulee = await paiement_service.annuler_course_systeme(db, course.id, RAISON)
        if annulee:
            await _prevenir_expediteur(db, annulee)
            n += 1
    return n


async def _prevenir_expediteur(db: AsyncSession, course: Course) -> None:
    from ..models.expediteur import Expediteur
    from ..models.user import User
    from .notification_service import notification_service
    try:
        user = (await db.execute(
            select(User).join(Expediteur, Expediteur.user_id == User.id)
            .where(Expediteur.id == course.expediteur_id)
        )).scalar_one_or_none()
        if user and user.device_token:
            await notification_service.envoyer_notification_push(
                user.device_token,
                titre="Course expirée",
                message=f"La course #{course.numero_course} n'a pas trouvé de livreur à temps. "
                        "Elle a été annulée et la commission vous a été rendue.",
                data={"type": "course_expiree", "course_id": str(course.id)},
            )
    except Exception:  # noqa: BLE001
        pass
