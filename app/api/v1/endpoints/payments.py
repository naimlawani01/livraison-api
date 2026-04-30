"""
Endpoints paiement GeniusPay.

POST /payments/commandes/{id}/relancer
    → Regénère un lien de paiement si le premier a expiré.

POST /payments/webhooks/geniuspay
    → Reçoit les notifications GeniusPay (payment.success, payout.completed, etc.)
    → Sécurisé par HMAC-SHA256.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.database import get_db
from ....models.commande import Commande, CommandeStatus, ModePaiement
from ....models.livreur import Livreur
from ....models.partenaire import Partenaire
from ....models.wallet_transaction import WalletTransaction
from ....services import genius_pay_service
from ....services.genius_pay_service import GeniusPayError
from ....services.matching_service import MatchingService
from ....utils.dependencies import get_current_partenaire

logger = logging.getLogger(__name__)
router = APIRouter()


# ── 1. Relancer un paiement expiré ───────────────────────────────────────────

@router.post("/commandes/{commande_id}/relancer", status_code=status.HTTP_200_OK)
async def relancer_paiement(
    commande_id: str,
    partenaire: Partenaire = Depends(get_current_partenaire),
    db: AsyncSession = Depends(get_db),
):
    """
    Génère un nouveau lien de paiement GeniusPay pour une commande MOBILE_MONEY
    dont le lien initial a expiré. Accessible uniquement par le partenaire propriétaire.
    """
    q = select(Commande).where(Commande.id == commande_id)
    r = await db.execute(q)
    commande: Optional[Commande] = r.scalar_one_or_none()

    if not commande:
        raise HTTPException(status_code=404, detail="Commande introuvable")
    if str(commande.partenaire_id) != str(partenaire.id):
        raise HTTPException(status_code=403, detail="Accès non autorisé")
    if commande.mode_paiement != ModePaiement.MOBILE_MONEY:
        raise HTTPException(status_code=400, detail="Cette commande n'est pas en mode Mobile Money")
    if commande.paiement_confirme == "oui":
        raise HTTPException(status_code=400, detail="Paiement déjà confirmé")
    if commande.status not in (CommandeStatus.CREEE,):
        raise HTTPException(status_code=400, detail=f"Impossible de relancer — statut: {commande.status}")

    try:
        paiement = await genius_pay_service.initier_paiement(
            commande_id=str(commande.id),
            partenaire_id=str(partenaire.id),
            montant=commande.prix_propose,
            description=f"Livraison {commande.numero_commande}",
            nom_client=commande.contact_client_nom,
        )
    except GeniusPayError as e:
        raise HTTPException(status_code=502, detail=str(e))

    commande.geniuspay_reference = paiement.get("reference")
    commande.geniuspay_checkout_url = paiement.get("checkout_url")
    await db.commit()

    return {
        "reference": commande.geniuspay_reference,
        "checkout_url": commande.geniuspay_checkout_url,
    }


# ── 2. Webhook GeniusPay ──────────────────────────────────────────────────────

@router.post("/webhooks/geniuspay", status_code=status.HTTP_200_OK)
async def webhook_geniuspay(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Point d'entrée des webhooks GeniusPay.
    Vérifie la signature HMAC avant tout traitement.

    Événements gérés :
      - payment.success  → confirme le paiement + diffuse la commande
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
        commande_id = metadata.get("commande_id")
        if not commande_id:
            logger.error("payment.success sans commande_id dans metadata")
            return {"ok": False, "reason": "missing_commande_id"}

        q = select(Commande).where(Commande.id == commande_id)
        r = await db.execute(q)
        commande: Optional[Commande] = r.scalar_one_or_none()

        if not commande:
            logger.error("payment.success — commande %s introuvable", commande_id)
            return {"ok": False, "reason": "commande_not_found"}

        if commande.paiement_confirme == "oui":
            logger.info("payment.success — commande %s déjà confirmée, skip", commande_id)
            return {"ok": True}

        # Confirmer le paiement
        commande.paiement_confirme = "oui"
        commande.geniuspay_reference = data.get("reference", commande.geniuspay_reference)
        await db.commit()

        # Récupérer le partenaire pour avoir ses coordonnées
        q_p = select(Partenaire).where(Partenaire.id == commande.partenaire_id)
        r_p = await db.execute(q_p)
        partenaire: Optional[Partenaire] = r_p.scalar_one_or_none()

        if partenaire:
            await MatchingService.diffuser_commande(
                db, commande, partenaire.latitude, partenaire.longitude
            )
        else:
            logger.error("payment.success — partenaire introuvable pour commande %s", commande_id)

        logger.info("payment.success — commande %s confirmée et diffusée", commande.numero_commande)
        return {"ok": True}

    # ── payment.failed ───────────────────────────────────────────────────────
    elif event == "payment.failed":
        commande_id = metadata.get("commande_id")
        logger.warning("payment.failed — commande_id=%s ref=%s", commande_id, data.get("reference"))
        return {"ok": True}

    # ── payment.expired ──────────────────────────────────────────────────────
    elif event == "payment.expired":
        commande_id = metadata.get("commande_id")
        logger.warning("payment.expired — commande_id=%s", commande_id)
        if commande_id:
            q = select(Commande).where(Commande.id == commande_id)
            r = await db.execute(q)
            commande: Optional[Commande] = r.scalar_one_or_none()
            if commande and commande.status == CommandeStatus.CREEE and commande.paiement_confirme == "non":
                commande.status = CommandeStatus.ANNULEE
                commande.annulee_at = datetime.now(timezone.utc)
                commande.raison_annulation = "Lien de paiement expiré"
                await db.commit()
                logger.info("payment.expired — commande %s annulée", commande_id)
        return {"ok": True}

    # ── cashout.completed ────────────────────────────────────────────────────
    elif event == "cashout.completed":
        livreur_id = metadata.get("livreur_id")
        reference = data.get("reference")
        if not reference:
            return {"ok": False, "reason": "missing_reference"}

        q = select(WalletTransaction).where(
            WalletTransaction.geniuspay_reference == reference
        )
        r = await db.execute(q)
        txn: Optional[WalletTransaction] = r.scalar_one_or_none()

        if txn:
            txn.statut = "complete"
            await db.commit()
            logger.info("cashout.completed — transaction %s marquée complete", reference)
        else:
            logger.warning("cashout.completed — transaction avec ref %s introuvable", reference)

        return {"ok": True}

    # ── cashout.failed ───────────────────────────────────────────────────────
    elif event == "cashout.failed":
        reference = data.get("reference")
        logger.error("cashout.failed — ref=%s livreur=%s", reference, metadata.get("livreur_id"))

        if reference:
            q = select(WalletTransaction).where(
                WalletTransaction.geniuspay_reference == reference
            )
            r = await db.execute(q)
            txn: Optional[WalletTransaction] = r.scalar_one_or_none()

            if txn and txn.statut == "en_cours":
                # Rembourser le solde livreur
                q_l = select(Livreur).where(Livreur.id == txn.livreur_id)
                r_l = await db.execute(q_l)
                livreur: Optional[Livreur] = r_l.scalar_one_or_none()
                if livreur:
                    livreur.solde_disponible += txn.montant
                txn.statut = "refuse"
                await db.commit()
                logger.info("cashout.failed — solde livreur remboursé de %s GNF", txn.montant)

        return {"ok": True}

    else:
        logger.info("Webhook GeniusPay — événement non géré: %s", event)
        return {"ok": True}
