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

from sqlalchemy import func, select, update
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
        # Délai compté depuis la (re)diffusion : une course remise à disposition
        # après la disparition d'un livreur repart pour un délai complet.
        select(Course).where(
            Course.status.in_(STATUTS_EXPIRABLES),
            func.coalesce(Course.diffusee_at, Course.created_at) < limite,
        )
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


async def surveiller_courses_en_cours(db: AsyncSession, maintenant: Optional[datetime] = None) -> dict:
    """Livreur qui disparaît.

    * Course ``ACCEPTEE`` depuis plus de ``DELAI_LIBERATION_COURSE_MINUTES`` et
      livreur sans position GPS récente sur la même durée (téléphone éteint,
      abandon) → la course est **remise à disposition** des autres livreurs.
      Aucun argent n'a encore changé de main à ce stade (la part livreur cash est
      remise à la récupération), donc rien à rembourser.
    * Course ``EN_RECUPERATION`` / ``EN_LIVRAISON`` depuis plus de
      ``DELAI_ALERTE_COURSE_MINUTES`` → **alerte** (log ERROR → Sentry) + push à
      l'expéditeur, une seule fois par course. Pas d'action automatique : le colis
      est peut-être entre les mains du livreur, c'est à l'admin de trancher.
    """
    from ..models.livreur import Livreur
    from .matching_service import MatchingService

    maintenant = maintenant or datetime.now(timezone.utc)
    seuil_lib = maintenant - timedelta(minutes=settings.DELAI_LIBERATION_COURSE_MINUTES)
    liberees = 0

    rows = (await db.execute(
        select(Course, Livreur)
        .join(Livreur, Livreur.id == Course.livreur_id)
        .where(Course.status == CourseStatus.ACCEPTEE, Course.acceptee_at < seuil_lib)
    )).all()
    for course, livreur in rows:
        if livreur.derniere_position_maj and livreur.derniere_position_maj >= seuil_lib:
            continue  # le livreur donne signe de vie : on le laisse finir
        res = await db.execute(
            update(Course)
            .where(Course.id == course.id, Course.status == CourseStatus.ACCEPTEE,
                   Course.livreur_id == livreur.id)
            .values(status=CourseStatus.CREEE, livreur_id=None, acceptee_at=None)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        if res.rowcount != 1:
            continue
        await _liberer_livreur_si_libre(db, livreur.id)
        course = (await db.execute(
            select(Course).where(Course.id == course.id).execution_options(populate_existing=True)
        )).scalar_one()
        expediteur = await _expediteur(db, course)
        if expediteur:
            await MatchingService.diffuser_course(
                db, course, expediteur.latitude, expediteur.longitude, expediteur_nom=expediteur.nom,
            )
        logger.warning("Course remise à disposition : livreur injoignable",
                       extra={"course_id": str(course.id), "livreur_id": str(livreur.id)})
        await _push_expediteur(db, course, "Nouveau livreur recherché",
                               f"Le livreur de la course #{course.numero_course} ne répond plus. "
                               "Nous cherchons un autre livreur.")
        liberees += 1

    seuil_alerte = maintenant - timedelta(minutes=settings.DELAI_ALERTE_COURSE_MINUTES)
    alertes = 0
    bloquees = (await db.execute(
        select(Course).where(
            Course.status.in_((CourseStatus.EN_RECUPERATION, CourseStatus.EN_LIVRAISON)),
            Course.acceptee_at < seuil_alerte,
        )
    )).scalars().all()
    for course in bloquees:
        if not await _premiere_alerte(course.id):
            continue
        logger.error("Course bloquée en livraison — vérification admin requise",
                     extra={"course_id": str(course.id), "numero": course.numero_course,
                            "livreur_id": str(course.livreur_id), "statut": course.status.value})
        await _push_expediteur(db, course, "Livraison anormalement longue",
                               f"La course #{course.numero_course} dure plus que prévu. "
                               "Le support Sönaiyaa a été alerté.")
        alertes += 1
    return {"liberees": liberees, "alertes": alertes}


async def _premiere_alerte(course_id) -> bool:
    """Vrai la première fois (clé Redis 24 h) — évite une alerte toutes les 5 min."""
    from ..core.redis import redis_client
    try:
        return bool(await redis_client.set(f"alerte_course:{course_id}", "1", nx=True, ex=86_400))
    except Exception:  # noqa: BLE001 — Redis en panne : on alerte quand même
        return True


async def _liberer_livreur_si_libre(db: AsyncSession, livreur_id) -> None:
    from ..models.livreur import Livreur
    actives = (await db.execute(select(func.count()).where(
        Course.livreur_id == livreur_id,
        Course.status.in_((CourseStatus.ACCEPTEE, CourseStatus.EN_RECUPERATION, CourseStatus.EN_LIVRAISON)),
    ))).scalar() or 0
    if actives == 0:
        await db.execute(update(Livreur).where(Livreur.id == livreur_id).values(is_en_course=False))
        await db.commit()


async def _expediteur(db: AsyncSession, course: Course):
    from ..models.expediteur import Expediteur
    return (await db.execute(select(Expediteur).where(Expediteur.id == course.expediteur_id))).scalar_one_or_none()


async def _push_expediteur(db: AsyncSession, course: Course, titre: str, message: str) -> None:
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
                user.device_token, titre=titre, message=message,
                data={"type": "course_surveillance", "course_id": str(course.id)},
            )
    except Exception:  # noqa: BLE001
        pass


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
