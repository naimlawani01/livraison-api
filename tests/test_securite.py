"""Failles de sécurité corrigées + livreur qui disparaît + code de livraison."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_resilience import _livreur
from tests.test_webhook import (  # noqa: F401 — fixtures réutilisées
    _CourseMobileMoney, _creer_expediteur, _stub_signature, session,
)


class TestInscription:
    def test_impossible_de_s_inscrire_admin(self):
        from pydantic import ValidationError
        from app.schemas.user import UserCreate
        with pytest.raises(ValidationError):
            UserCreate(phone="620000000", role="ADMIN")

    def test_expediteur_et_livreur_autorises(self):
        from app.schemas.user import UserCreate
        assert UserCreate(phone="620000000", role="EXPEDITEUR").role.value == "EXPEDITEUR"
        assert UserCreate(phone="620000001", role="LIVREUR").role.value == "LIVREUR"


class TestEndpointsProteges:
    @pytest.mark.parametrize("module, nom", [
        ("app.api.v1.endpoints.livreurs", "list_livreurs"),
        ("app.api.v1.endpoints.livreurs", "get_livreur"),
        ("app.api.v1.endpoints.expediteurs", "list_expediteurs"),
        ("app.api.v1.endpoints.expediteurs", "get_expediteur"),
    ])
    def test_donnees_personnelles_reservees_admin(self, module, nom):
        import importlib
        import inspect
        from app.utils.dependencies import get_current_admin
        fn = getattr(importlib.import_module(module), nom)
        deps = [p.default.dependency for p in inspect.signature(fn).parameters.values()
                if hasattr(p.default, "dependency")]
        assert get_current_admin in deps

    def test_livreur_ne_peut_pas_injecter_une_url_de_document(self):
        from app.schemas.livreur import LivreurUpdate
        data = LivreurUpdate(piece_identite_url="https://piege.example").model_dump(exclude_unset=True)
        assert "piece_identite_url" not in data


class TestFraudeEtAcces(_CourseMobileMoney):
    async def test_expediteur_verifie_ne_deplace_pas_son_point_de_retrait(self, session):
        from fastapi import HTTPException
        from app.api.v1.endpoints.expediteurs import update_my_expediteur
        from app.schemas.expediteur import ExpediteurUpdate
        _, p = await _creer_expediteur(session)
        with pytest.raises(HTTPException) as exc:
            await update_my_expediteur(ExpediteurUpdate(latitude=9.60), p, session)
        assert exc.value.status_code == 400
        # les autres champs restent modifiables
        await update_my_expediteur(ExpediteurUpdate(nom="Boutique Kaloum"), p, session)
        assert p.nom == "Boutique Kaloum"

    async def test_lien_de_suivi_reserve_au_proprietaire(self, session):
        from fastapi import HTTPException
        from app.api.v1.endpoints.tracking import generate_tracking_link
        from app.models.course import Payeur
        _, p, cmd = await self._creer_course(session, Payeur.EXPEDITEUR)
        u_liv, _ = await _livreur(session)
        with pytest.raises(HTTPException) as exc:
            await generate_tracking_link(cmd.id, u_liv, session)
        assert exc.value.status_code == 403


class TestCodeLivraison:
    def test_code_active_par_defaut(self):
        from app.schemas.course import CourseCreate
        c = CourseCreate(contact_client_nom="Client", contact_client_telephone="620000000", prix_propose=1)
        assert c.exige_code_livraison is True

    async def test_code_envoye_au_client_par_sms(self, monkeypatch):
        from app.services.sms_service import sms_service
        envoyes = []

        async def _send(tel, msg):
            envoyes.append(msg)
            return True
        monkeypatch.setattr(sms_service, "_send", _send)
        await sms_service.envoyer_sms_course(
            telephone="620000000", nom_client="Awa", numero_course="C-1", expediteur_nom="Boutique",
            montant=0, tracking_url="https://x/suivi/t", code_livraison="4821",
        )
        assert "4821" in envoyes[0]


class TestLivreurQuiDisparait(_CourseMobileMoney):
    async def _course_acceptee(self, session, statut, il_y_a, position_recente):
        from app.models.course import CourseStatus, Payeur
        _, p, cmd = await self._creer_course(session, Payeur.EXPEDITEUR)
        _, liv = await _livreur(session)
        maintenant = datetime.now(timezone.utc)
        cmd.livreur_id, cmd.status = liv.id, CourseStatus(statut)
        cmd.acceptee_at = maintenant - il_y_a
        liv.is_en_course = True
        liv.derniere_position_maj = maintenant - (timedelta(minutes=2) if position_recente else timedelta(hours=2))
        await session.commit()
        return cmd, liv

    async def test_livreur_injoignable_course_remise_a_disposition(self, session):
        from app.services.expiration_service import surveiller_courses_en_cours
        cmd, liv = await self._course_acceptee(session, "ACCEPTEE", timedelta(minutes=45), False)
        res = await surveiller_courses_en_cours(session)
        await session.refresh(cmd)
        await session.refresh(liv)
        assert res["liberees"] == 1
        assert cmd.livreur_id is None and cmd.status.value in ("CREEE", "DIFFUSEE")
        assert liv.is_en_course is False

    async def test_livreur_qui_bouge_garde_sa_course(self, session):
        from app.services.expiration_service import surveiller_courses_en_cours
        cmd, liv = await self._course_acceptee(session, "ACCEPTEE", timedelta(minutes=45), True)
        assert (await surveiller_courses_en_cours(session))["liberees"] == 0
        await session.refresh(cmd)
        assert cmd.livreur_id == liv.id

    async def test_livraison_trop_longue_alerte(self, session):
        from app.services.expiration_service import surveiller_courses_en_cours
        cmd, _ = await self._course_acceptee(session, "EN_LIVRAISON", timedelta(hours=4), True)
        res = await surveiller_courses_en_cours(session)
        assert res["alertes"] == 1
        await session.refresh(cmd)
        assert cmd.status.value == "EN_LIVRAISON"   # aucune action automatique


# ── Failles restantes : 2FA admin, squat de numéro, rate-limit, fraude livraison ─

def _req(path="/api/v1/auth/login", headers=None):
    from starlette.requests import Request

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "POST", "path": path, "headers": hdrs,
                    "query_string": b"", "client": (f"10.0.{uuid.uuid4().int % 250}.1", 1234)}, receive)


async def _user(session, role, verified=True, password=None, age=timedelta(0)):
    from app.core.security import get_password_hash
    from app.models.user import User, UserRole
    u = User(id=uuid.uuid4(), phone=f"+22462{uuid.uuid4().int % 10_000_000:07d}", role=UserRole(role),
             is_verified=verified, password_hash=get_password_hash(password) if password else None,
             created_at=datetime.now(timezone.utc) - age)
    session.add(u)
    await session.commit()
    return u


@pytest.fixture
def sms_captures(monkeypatch):
    from app.services.sms_service import sms_service
    envoyes = []

    async def _otp(phone, code):
        envoyes.append(code)
        return True
    monkeypatch.setattr(sms_service, "envoyer_otp", _otp)
    return envoyes


class TestAdmin2FA:
    async def test_mot_de_passe_seul_ne_suffit_pas(self, session, sms_captures):
        from fastapi import HTTPException
        from app.api.v1.endpoints.auth import login
        from app.schemas.user import UserLogin
        admin = await _user(session, "ADMIN", password="MotDePasseAdmin!2026")
        with pytest.raises(HTTPException) as exc:
            await login(_req(), UserLogin(phone=admin.phone, password="MotDePasseAdmin!2026"), session)
        assert exc.value.detail == "otp_required"
        assert len(sms_captures) == 1
        # mot de passe + bon code → tokens
        res = await login(_req(), UserLogin(phone=admin.phone, password="MotDePasseAdmin!2026",
                                            otp_code=sms_captures[0]), session)
        assert res.access_token

    async def test_mauvais_code_refuse(self, session, sms_captures):
        from fastapi import HTTPException
        from app.api.v1.endpoints.auth import login
        from app.schemas.user import UserLogin
        admin = await _user(session, "ADMIN", password="MotDePasseAdmin!2026")
        with pytest.raises(HTTPException):
            await login(_req(), UserLogin(phone=admin.phone, password="MotDePasseAdmin!2026"), session)
        with pytest.raises(HTTPException) as exc:
            await login(_req(), UserLogin(phone=admin.phone, password="MotDePasseAdmin!2026",
                                          otp_code="000000" if sms_captures[0] != "000000" else "111111"), session)
        assert exc.value.status_code == 401

    async def test_admin_ne_se_connecte_pas_par_sms_seul(self, session):
        from fastapi import HTTPException
        from app.api.v1.endpoints.auth import verify_otp
        from app.schemas.user import OTPVerify
        admin = await _user(session, "ADMIN", password="MotDePasseAdmin!2026")
        admin.otp_code, admin.otp_expires_at = "123456", datetime.now(timezone.utc) + timedelta(minutes=5)
        await session.commit()
        with pytest.raises(HTTPException) as exc:
            await verify_otp(_req("/api/v1/auth/verify-otp"), OTPVerify(phone=admin.phone, otp_code="123456"), session)
        assert exc.value.status_code == 403


class TestSquatNumero:
    async def test_compte_non_verifie_ancien_est_remplace(self, session):
        from app.api.v1.endpoints.auth import register
        from app.schemas.user import UserCreate
        squat = await _user(session, "LIVREUR", verified=False, password="Squatteur!2026x", age=timedelta(hours=2))
        phone = squat.phone
        res = await register(_req("/api/v1/auth/register"), UserCreate(phone=phone, role="EXPEDITEUR"), session)
        assert res.user.role.value == "EXPEDITEUR"

    async def test_compte_verifie_reste_protege(self, session):
        from fastapi import HTTPException
        from app.api.v1.endpoints.auth import register
        from app.schemas.user import UserCreate
        u = await _user(session, "LIVREUR", verified=True)
        with pytest.raises(HTTPException):
            await register(_req("/api/v1/auth/register"), UserCreate(phone=u.phone, role="LIVREUR"), session)

    async def test_mot_de_passe_plante_efface_a_la_verification_tardive(self, session):
        from app.api.v1.endpoints.auth import verify_otp
        from app.schemas.user import OTPVerify
        u = await _user(session, "EXPEDITEUR", verified=False, password="Squatteur!2026x", age=timedelta(hours=2))
        u.otp_code, u.otp_expires_at = "654321", datetime.now(timezone.utc) + timedelta(minutes=5)
        await session.commit()
        await verify_otp(_req("/api/v1/auth/verify-otp"), OTPVerify(phone=u.phone, otp_code="654321"), session)
        await session.refresh(u)
        assert u.is_verified and u.password_hash is None


class TestRateLimitParUtilisateur:
    def test_requete_authentifiee_comptee_par_utilisateur(self):
        from app.core.rate_limit import _key_func
        from app.core.security import create_access_token
        token = create_access_token({"sub": "abc", "role": "LIVREUR"})
        assert _key_func(_req(headers={"Authorization": f"Bearer {token}"})) == "user:abc"

    def test_token_forge_retombe_sur_l_ip(self):
        from app.core.rate_limit import _key_func
        assert not _key_func(_req(headers={"Authorization": "Bearer faux.token.x"})).startswith("user:")


class TestFraudeLivraison(_CourseMobileMoney):
    async def test_ecart_enregistre_quand_livre_loin_de_l_adresse(self, session):
        from app.api.v1.endpoints.courses import update_course_status
        from app.models.course import CourseStatus, Payeur
        _, p, cmd = await self._creer_course(session, Payeur.CLIENT)
        _, liv = await _livreur(session)
        cmd.livreur_id, cmd.status, cmd.paiement_confirme = liv.id, CourseStatus.EN_LIVRAISON, "oui"
        cmd.exige_code_livraison = False
        cmd.latitude_client, cmd.longitude_client = 9.5400, -13.6800
        liv.latitude, liv.longitude = 9.6400, -13.5800   # ~15 km plus loin
        await session.commit()
        await update_course_status(cmd.id, CourseStatus.TERMINEE, None, liv, session)
        await session.refresh(cmd)
        assert cmd.ecart_livraison_km > 10
