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
