"""Expiration automatique des courses.

Une course encore ``CREEE`` ou ``DIFFUSEE`` (aucun livreur ne l'a acceptée, ou
le client n'a jamais payé / partagé sa position) après
``settings.COURSE_EXPIRATION_MINUTES`` est annulée : la commission réservée est
rendue au Crédit de l'expéditeur, et un paiement Mobile Money éventuel est
remboursé (avoir ou remboursement client tracé).

Lancé toutes les 5 min par une tâche de fond (``main.py``), avec un verrou Redis
pour qu'un seul worker uvicorn le fasse à la fois.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..models.course import Course, CourseStatus
from . import credit_service, remboursement_service

logger = logging.getLogger(__name__)

STATUTS_EXPIRABLES = (CourseStatus.CREEE, CourseStatus.DIFFUSEE)


async def expirer_courses(db: AsyncSession, maintenant: Optional[datetime] = None) -> int:
    """Annule les courses en attente depuis trop longtemps. Retourne leur nombre."""
    maintenant = maintenant or datetime.now(timezone.utc)
    limite = maintenant - timedelta(minutes=settings.COURSE_EXPIRATION_MINUTES)
    ids = (await db.execute(
        select(Course.id).where(Course.status.in_(STATUTS_EXPIRABLES), Course.created_at < limite)
    )).scalars().all()

    n = 0
    for course_id in ids:
        # UPDATE conditionnel = atomique : si un livreur accepte au même moment,
        # le statut a changé et on ne touche à rien.
        res = await db.execute(
            update(Course)
            .where(Course.id == course_id, Course.status.in_(STATUTS_EXPIRABLES))
            .values(
                status=CourseStatus.ANNULEE,
                annulee_at=maintenant,
                raison_annulation="Expirée : aucun livreur ou paiement dans le délai",
            )
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        if res.rowcount != 1:
            continue
        course = (await db.execute(select(Course).where(Course.id == course_id))).scalar_one()
        await db.refresh(course)
        try:
            await credit_service.restituer_commission(
                db, course.expediteur_id, course.id,
                description=f"Remboursement course expirée #{course.numero_course}",
            )
            await remboursement_service.enregistrer_remboursement(db, course)
        except Exception as e:  # noqa: BLE001
            logger.error("Expiration — restitution échouée", extra={"course_id": str(course_id), "erreur": str(e)})
        await _prevenir_expediteur(db, course)
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
