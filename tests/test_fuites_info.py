"""Informations renvoyées qu'un attaquant pourrait exploiter."""
import logging

import pytest

from tests.test_resilience import _livreur
from tests.test_securite import _req, _user
from tests.test_webhook import (  # noqa: F401 — fixtures réutilisées
    _CourseMobileMoney, _creer_expediteur, _stub_signature, session,
)


class TestEnumerationComptes:
    async def test_request_otp_meme_reponse_numero_inconnu(self, session, monkeypatch):
        from app.api.v1.endpoints.auth import request_otp
        from app.schemas.user import OTPRequest
        from app.services.sms_service import sms_service

        async def _otp(*a, **k):
            return True
        monkeypatch.setattr(sms_service, "envoyer_otp", _otp)
        connu = await _user(session, "EXPEDITEUR")
        r_connu = await request_otp(_req("/api/v1/auth/request-otp"), OTPRequest(phone=connu.phone), session)
        r_inconnu = await request_otp(_req("/api/v1/auth/request-otp"), OTPRequest(phone="629999999"), session)
        assert r_connu == r_inconnu

    async def test_verify_otp_numero_inconnu_comme_mauvais_code(self, session):
        from fastapi import HTTPException
        from app.api.v1.endpoints.auth import verify_otp
        from app.schemas.user import OTPVerify
        with pytest.raises(HTTPException) as exc:
            await verify_otp(_req("/api/v1/auth/verify-otp"), OTPVerify(phone="628888888", otp_code="123456"), session)
        assert exc.value.status_code == 400 and exc.value.detail == "Code OTP invalide"


class TestDonneesClient:
    def test_course_proposee_sans_donnees_personnelles(self):
        from app.api.v1.endpoints.courses import masquer_client_avant_acceptation
        data = masquer_client_avant_acceptation({
            "contact_client_telephone": "+224622334455", "contact_client_nom": "Awa Camara",
            "adresse_client": "Kaloum, maison bleue", "instructions_speciales": "Appeler au 622…",
            "latitude_client": 9.512345, "longitude_client": -13.712345,
            "location_token": "loc", "tracking_token": "trk", "geniuspay_checkout_url": "https://pay",
            "code_livraison": "1234",
        })
        assert data["contact_client_telephone"].endswith("55") and "2233" not in data["contact_client_telephone"]
        assert data["contact_client_nom"] == "Awa"
        assert data["adresse_client"] is None and data["instructions_speciales"] is None
        assert data["latitude_client"] == 9.51
        assert data["location_token"] is None and data["tracking_token"] is None
        assert data["geniuspay_checkout_url"] is None and "code_livraison" not in data


class TestVueLivreur(_CourseMobileMoney):
    async def test_livreur_ne_recoit_jamais_les_jetons(self, session):
        from app.api.v1.endpoints.courses import update_course_status
        from app.models.course import CourseStatus, Payeur
        _, p, cmd = await self._creer_course(session, Payeur.CLIENT)
        _, liv = await _livreur(session)
        cmd.livreur_id, cmd.status = liv.id, CourseStatus.ACCEPTEE
        cmd.geniuspay_checkout_url = "https://pay.example/x"
        await session.commit()
        res = await update_course_status(cmd.id, CourseStatus.EN_RECUPERATION, None, liv, session)
        assert res["location_token"] is None and res["tracking_token"] is None
        assert res["geniuspay_checkout_url"] is None

    async def test_suivi_public_sans_livreur_apres_livraison(self, session):
        from app.api.v1.endpoints.tracking import tracking_status
        from app.models.course import CourseStatus, Payeur
        _, p, cmd = await self._creer_course(session, Payeur.EXPEDITEUR)
        _, liv = await _livreur(session)
        liv.latitude, liv.longitude = 9.6, -13.6
        cmd.livreur_id, cmd.status = liv.id, CourseStatus.TERMINEE
        await session.commit()
        res = await tracking_status(cmd.tracking_token, session)
        assert res["livreur_telephone"] is None and res["livreur_position"] is None


def test_sms_mode_dev_ne_logue_pas_les_codes(caplog):
    import asyncio
    from app.services.sms_service import SMSService
    svc = SMSService()
    svc._configured = False
    with caplog.at_level(logging.INFO):
        asyncio.run(svc._send("620000000", "Votre code : 482913"))
    assert "482913" not in caplog.text
