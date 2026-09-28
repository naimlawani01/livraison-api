"""Défaillances courantes : double acceptation, double fin de course, webhook
perdu (réconciliation PSP), retrait bloqué, lien expiré, panne Redis."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_webhook import (  # noqa: F401 — fixtures réutilisées
    _CourseMobileMoney, _course_payload, _creer_expediteur, _make_request,
    _stub_signature, session,
)


async def _livreur(session, solde=0.0):
    from app.models.livreur import Livreur
    from app.models.user import User, UserRole
    u = User(id=uuid.uuid4(), phone=f"+224601{uuid.uuid4().int % 1000000:06d}",
             role=UserRole.LIVREUR, is_verified=True)
    session.add(u)
    await session.flush()
    liv = Livreur(id=uuid.uuid4(), user_id=u.id, nom_complet="Sow", is_verified=True,
                  is_disponible=True, solde_disponible=solde, total_gains=0.0)
    session.add(liv)
    await session.commit()
    return u, liv


class TestConcurrence(_CourseMobileMoney):
    async def test_deux_livreurs_un_seul_gagne(self, session):
        from sqlalchemy.ext.asyncio import AsyncSession
        from app.models.course import Course, CourseStatus, Payeur
        from app.services.matching_service import MatchingService
        _, p, cmd = await self._creer_course(session, Payeur.EXPEDITEUR)
        cmd.status = CourseStatus.DIFFUSEE
        await session.commit()
        _, liv_a = await _livreur(session)
        _, liv_b = await _livreur(session)

        async with AsyncSession(bind=session.bind, expire_on_commit=False) as s2:
            # B a chargé la course (encore DIFFUSEE) avant que A n'accepte
            cmd_vu_par_b = await s2.get(Course, cmd.id)
            liv_b2 = await s2.get(type(liv_b), liv_b.id)
            assert await MatchingService.accepter_course(session, cmd, liv_a) is True
            assert await MatchingService.accepter_course(s2, cmd_vu_par_b, liv_b2) is False
        await session.refresh(cmd)
        assert cmd.livreur_id == liv_a.id

    async def test_double_terminee_credite_une_seule_fois(self, session):
        from fastapi import HTTPException
        from app.api.v1.endpoints.courses import update_course_status
        from app.models.course import CourseStatus, Payeur
        _, p, cmd = await self._creer_course(session, Payeur.CLIENT)
        _, liv = await _livreur(session)
        cmd.livreur_id, cmd.status, cmd.paiement_confirme = liv.id, CourseStatus.EN_LIVRAISON, "oui"
        await session.commit()
        await update_course_status(cmd.id, CourseStatus.TERMINEE, None, liv, session)
        with pytest.raises(HTTPException):   # la requête rejouée est refusée
            await update_course_status(cmd.id, CourseStatus.TERMINEE, None, liv, session)
        await session.refresh(liv)
        assert liv.solde_disponible == cmd.montant_livreur


class TestReconciliation(_CourseMobileMoney):
    async def _course_mm_ancienne(self, session):
        from app.models.course import Payeur
        _, p, cmd = await self._creer_course(session, Payeur.CLIENT)
        cmd.geniuspay_reference = "MTX-REF"
        cmd.created_at = datetime.now(timezone.utc) - timedelta(hours=3)
        await session.commit()
        return p, cmd

    @pytest.mark.parametrize("statut_psp, attendu_statut, attendu_paye", [
        ("success", "CREEE", "oui"),      # webhook perdu mais payé → confirmé, pas annulé
        ("pending", "ANNULEE", "non"),    # jamais payé → expire normalement
    ])
    async def test_expiration_interroge_le_psp(self, session, monkeypatch, statut_psp, attendu_statut, attendu_paye):
        import app.services.genius_pay_service as gps
        from app.services.expiration_service import expirer_courses

        async def _get(ref):
            return {"status": statut_psp}
        monkeypatch.setattr(gps, "get_paiement", _get)
        _, cmd = await self._course_mm_ancienne(session)
        await expirer_courses(session)
        await session.refresh(cmd)
        assert cmd.status.value == attendu_statut
        assert cmd.paiement_confirme == attendu_paye

    async def test_psp_injoignable_on_n_annule_pas(self, session, monkeypatch):
        import app.services.genius_pay_service as gps
        from app.services.expiration_service import expirer_courses

        async def _panne(ref):
            raise gps.GeniusPayError("timeout")
        monkeypatch.setattr(gps, "get_paiement", _panne)
        _, cmd = await self._course_mm_ancienne(session)
        assert await expirer_courses(session) == 0
        await session.refresh(cmd)
        assert cmd.status.value == "CREEE"

    @pytest.mark.parametrize("statut_psp, statut_txn, solde", [
        ("failed", "refuse", 20_000),       # échec → Gains recrédités
        ("completed", "complete", 0),       # succès → retrait terminé
    ])
    async def test_retrait_bloque_reconcilie(self, session, monkeypatch, statut_psp, statut_txn, solde):
        import app.services.genius_pay_service as gps
        from app.models.wallet_transaction import WalletTransaction
        from app.services.reconciliation_service import reconcilier_retraits

        async def _get(ref):
            return {"status": statut_psp}
        monkeypatch.setattr(gps, "get_payout", _get)
        _, liv = await _livreur(session, solde=0.0)
        txn = WalletTransaction(
            livreur_id=liv.id, type="retrait", montant=20_000, solde_avant=20_000, solde_apres=0,
            description="Retrait", statut="en_cours", geniuspay_reference="PYT-1",
            created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        session.add(txn)
        await session.commit()
        assert await reconcilier_retraits(session) == 1
        assert await reconcilier_retraits(session) == 0   # idempotent
        await session.refresh(txn)
        await session.refresh(liv)
        assert txn.statut == statut_txn
        assert liv.solde_disponible == solde


class TestLienExpire(_CourseMobileMoney):
    async def test_lien_expire_rend_la_commission(self, session):
        from app.api.v1.endpoints.payments import webhook_geniuspay
        from app.models.course import Payeur
        from app.services import credit_service
        _, p, cmd = await self._creer_course(session, Payeur.CLIENT)
        assert await credit_service.credit_disponible(session, p.id) == 48_800
        payload = {"event": "payment.expired", "data": {"metadata": {"course_id": str(cmd.id)}}}
        await webhook_geniuspay(_make_request(payload), session)
        await session.refresh(cmd)
        assert cmd.status.value == "ANNULEE"
        assert await credit_service.credit_disponible(session, p.id) == 50_000


class TestPanneRedis:
    async def test_blacklist_jwt_ne_fait_pas_tomber_l_api(self, monkeypatch):
        from app.core import redis as redis_mod
        from app.core.security import is_token_blacklisted

        async def _panne(*a, **k):
            raise ConnectionError("redis down")
        monkeypatch.setattr(redis_mod.redis_client, "exists", _panne)
        assert await is_token_blacklisted("jti") is False

    async def test_compteur_code_livraison_bascule_en_memoire(self, monkeypatch):
        from app.core import compteur

        async def _panne(*a, **k):
            raise ConnectionError("redis down")
        for m in ("get", "incr", "expire", "delete"):
            monkeypatch.setattr(compteur.redis_client, m, _panne)
        cle = f"code_attempts:{uuid.uuid4()}"
        for _ in range(5):
            await compteur.incrementer(cle, 900)
        assert await compteur.lire(cle) == 5          # la limite reste appliquée
        await compteur.effacer(cle)
        assert await compteur.lire(cle) == 0
