"""
Endpoints paiement GeniusPay.

POST /payments/courses/{id}/relancer
    → Regénère un lien de paiement si le premier a expiré.

POST /payments/webhooks/geniuspay
    → Reçoit les notifications GeniusPay (payment.success, payout.completed, etc.)
    → Sécurisé par HMAC-SHA256.
"""
import json
import logging
import uuid
from typing import Optional

from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.database import get_db
from ....models.course import Course, CourseStatus, ModePaiement, Payeur
from ....models.expediteur import Expediteur
from ....models.credit_transaction import CreditTransaction
from ....services import genius_pay_service, credit_service, paiement_service, soldes
from ....services.genius_pay_service import GeniusPayError
from ....core.rate_limit import limiter
from ....utils.dependencies import get_current_expediteur

logger = logging.getLogger(__name__)
router = APIRouter()


# ── 1. Relancer un paiement expiré ───────────────────────────────────────────

@router.post("/courses/{course_id}/relancer", status_code=status.HTTP_200_OK)
async def relancer_paiement(
    course_id: UUID,
    expediteur: Expediteur = Depends(get_current_expediteur),
    db: AsyncSession = Depends(get_db),
):
    """
    Génère un nouveau lien de paiement GeniusPay pour une course MOBILE_MONEY
    dont le lien initial a expiré. Accessible uniquement par le expediteur propriétaire.
    """
    q = select(Course).where(Course.id == course_id)
    r = await db.execute(q)
    course: Optional[Course] = r.scalar_one_or_none()

    if not course:
        raise HTTPException(status_code=404, detail="Course introuvable")
    if str(course.expediteur_id) != str(expediteur.id):
        raise HTTPException(status_code=403, detail="Accès non autorisé")
    if course.mode_paiement != ModePaiement.MOBILE_MONEY:
        raise HTTPException(status_code=400, detail="Cette course n'est pas en mode Mobile Money")
    if course.paiement_confirme == "oui":
        raise HTTPException(status_code=400, detail="Paiement déjà confirmé")
    if course.status not in (CourseStatus.CREEE,):
        raise HTTPException(status_code=400, detail=f"Impossible de relancer — statut: {course.status}")

    # Expéditeur payeur : la commission doit être entièrement couverte avant de
    # générer le lien (sinon Sönaiyaa perdrait le complément après recalcul GPS).
    if course.payeur == Payeur.EXPEDITEUR.value and await credit_service.commission_reservee(db, course.id) > 0:
        try:
            await credit_service.completer_commission(db, course)
        except soldes.SoldeInsuffisant:
            raise HTTPException(
                status_code=400,
                detail="Crédit insuffisant pour couvrir la commission. Rechargez votre Crédit.",
            )

    # Idempotency : verrou Redis 60s pour éviter de générer plusieurs
    # références GeniusPay sur un double-clic ou un retry réseau. Si le
    # même expediteur relance la même course dans les 60s, on retourne
    # le lien existant sans rappeler GeniusPay.
    from ....core.redis import redis_client
    lock_key = f"relancer_lock:{course_id}"
    try:
        lock_acquired = await redis_client.set(lock_key, "1", nx=True, ex=60)
    except Exception:  # noqa: BLE001 — Redis en panne : on relance sans verrou
        lock_acquired = True

    if not lock_acquired and course.geniuspay_reference and course.geniuspay_checkout_url:
        # Double-clic / retry — retourne le lien existant tel quel.
        return {
            "reference": course.geniuspay_reference,
            "checkout_url": course.geniuspay_checkout_url,
            "idempotent_replay": True,
        }

    try:
        paiement = await genius_pay_service.initier_paiement(
            course_id=str(course.id),
            expediteur_id=str(expediteur.id),
            montant=course.montant_a_encaisser,
            description=f"Livraison {course.numero_course}",
            nom_client=course.contact_client_nom,
        )
    except GeniusPayError as e:
        # Libère le verrou si GeniusPay rejette, sinon on resterait bloqué 60s
        try:
            await redis_client.delete(lock_key)
        except Exception:  # noqa: BLE001
            pass
        # Le détail brut du PSP (corps de réponse) reste dans les logs, jamais
        # renvoyé au client (fuite d'informations internes).
        logger.error("GeniusPay relance échouée", extra={"course_id": str(course_id), "erreur": str(e)})
        raise HTTPException(status_code=502, detail="Le service de paiement est indisponible. Réessayez plus tard.")

    course.geniuspay_reference = paiement.get("reference")
    course.geniuspay_checkout_url = paiement.get("checkout_url")
    await db.commit()

    return {
        "reference": course.geniuspay_reference,
        "checkout_url": course.geniuspay_checkout_url,
    }


# ── 2. Webhook GeniusPay ──────────────────────────────────────────────────────

@router.post("/webhooks/geniuspay", status_code=status.HTTP_200_OK)
@limiter.exempt  # appels du PSP (rafales possibles) — protégé par signature HMAC
async def webhook_geniuspay(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Point d'entrée des webhooks GeniusPay.
    Vérifie la signature HMAC avant tout traitement.

    Événements gérés :
      - payment.success  → confirme le paiement + diffuse la course
      - payment.failed   → log uniquement
      - payout.completed → marque le retrait livreur comme terminé
      - payout.failed    → rembourse le solde livreur
    """
    payload_bytes = await request.body()
    signature = request.headers.get("X-Webhook-Signature", "")
    timestamp = request.headers.get("X-Webhook-Timestamp", "")

    # Vérification de la signature (rejet silencieux = 200 pour éviter le retry inutile)
    if not genius_pay_service.verify_webhook_signature(payload_bytes, signature, timestamp):
        logger.warning("Webhook GeniusPay — signature invalide (ip=%s)", request.client.host if request.client else "?")
        # On retourne 200 pour ne pas déclencher les retries GeniusPay,
        # mais on n'effectue aucune action.
        return {"ok": False, "reason": "invalid_signature"}

    try:
        body = json.loads(payload_bytes)
    except json.JSONDecodeError:
        logger.error("Webhook GeniusPay — payload JSON invalide")
        return {"ok": False, "reason": "invalid_json"}

    event = body.get("event")
    data = body.get("data", {})
    metadata = data.get("metadata", {})

    logger.info("Webhook GeniusPay reçu: event=%s ref=%s", event, data.get("reference"))

    # ── payment.success ──────────────────────────────────────────────────────
    if event == "payment.success":
        # Cas 1 : recharge du Crédit d'un expéditeur (pas de course liée)
        if metadata.get("type") == "credit_recharge":
            expediteur_id = metadata.get("expediteur_id")
            reference = data.get("reference")
            montant = data.get("amount") or metadata.get("montant")
            if not expediteur_id or not montant:
                logger.error("credit_recharge — expediteur_id/montant manquant (ref=%s)", reference)
                return {"ok": False, "reason": "missing_credit_fields"}

            # expediteur_id vient du JSON (string) → UUID validé (robuste, dialect-agnostic).
            import uuid as _uuid
            try:
                expediteur_id = _uuid.UUID(str(expediteur_id))
            except (ValueError, TypeError):
                logger.error("credit_recharge — expediteur_id invalide: %s", expediteur_id)
                return {"ok": False, "reason": "bad_expediteur_id"}

            # Idempotence : recharge déjà appliquée pour cette référence ?
            if reference:
                dup = await db.execute(
                    select(CreditTransaction).where(
                        CreditTransaction.geniuspay_reference == reference
                    )
                )
                if dup.scalar_one_or_none():
                    logger.info("credit_recharge — ref %s déjà appliquée, skip", reference)
                    return {"ok": True}

            try:
                await credit_service.recharger(
                    db, expediteur_id, float(montant),
                    description="Recharge Mobile Money",
                    geniuspay_reference=reference,
                )
                logger.info("credit_recharge — Crédit +%s GNF (expediteur=%s)", montant, expediteur_id)
            except Exception as e:  # noqa: BLE001
                logger.error("credit_recharge — échec application: %s", e)
                return {"ok": False, "reason": "credit_apply_failed"}
            return {"ok": True}

        # Cas 2 : paiement d'une course Mobile Money
        course_id = metadata.get("course_id")
        if not course_id:
            logger.error("payment.success sans course_id dans metadata")
            return {"ok": False, "reason": "missing_course_id"}
        try:
            course_uuid = uuid.UUID(str(course_id))
        except ValueError:
            logger.error("payment.success — course_id invalide: %s", course_id)
            return {"ok": False, "reason": "bad_course_id"}

        q = select(Course).where(Course.id == course_uuid)
        r = await db.execute(q)
        course: Optional[Course] = r.scalar_one_or_none()

        if not course:
            logger.error("payment.success — course %s introuvable", course_id)
            return {"ok": False, "reason": "course_not_found"}

        await paiement_service.confirmer_paiement_course(db, course, data.get("reference"))
        logger.info("payment.success — course %s traitée", course.numero_course)
        return {"ok": True}

    # ── payment.failed ───────────────────────────────────────────────────────
    elif event == "payment.failed":
        course_id = metadata.get("course_id")
        logger.warning("payment.failed — course_id=%s ref=%s", course_id, data.get("reference"))
        return {"ok": True}

    # ── payment.expired ──────────────────────────────────────────────────────
    elif event == "payment.expired":
        course_id = metadata.get("course_id")
        logger.warning("payment.expired — course_id=%s", course_id)
        if course_id:
            try:
                course_uuid = uuid.UUID(str(course_id))
            except ValueError:
                return {"ok": False, "reason": "bad_course_id"}
            course = (await db.execute(select(Course).where(Course.id == course_uuid))).scalar_one_or_none()
            if course and course.paiement_confirme == "non":
                # Annulation + commission rendue (avant : commission perdue).
                if await paiement_service.annuler_course_systeme(
                    db, course.id, "Lien de paiement expiré", statuts=(CourseStatus.CREEE,),
                ):
                    logger.info("payment.expired — course %s annulée", course_id)
        return {"ok": True}

    # ── cashout.completed ────────────────────────────────────────────────────
    elif event in ("cashout.completed", "cashout.failed"):
        reference = data.get("reference")
        if not reference:
            return {"ok": False, "reason": "missing_reference"}
        succes = event == "cashout.completed"
        if not succes:
            logger.error("cashout.failed — ref=%s livreur=%s", reference, metadata.get("livreur_id"))
        if not await paiement_service.finaliser_retrait(db, reference, succes):
            logger.warning("%s — transaction %s introuvable ou déjà traitée", event, reference)
        return {"ok": True}

    else:
        logger.info("Webhook GeniusPay — événement non géré: %s", event)
        return {"ok": True}
