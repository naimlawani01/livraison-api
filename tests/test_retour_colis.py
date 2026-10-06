"""Livraison impossible (client absent / refus) et retour du colis.

Règles testées :
* garde-fous anti « faux échec » : présence chez le client, attente minimale ;
* trajet aller dû (part livreur acquise), commission non rendue ;
* frais de retour = TAUX_FRAIS_RETOUR × prix, pris sur le Crédit, plafonnés ;
* reste dû → création de course bloquée, versé au livreur à la recharge ;
* plus d'annulation une fois le colis en retour (sauf admin).
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

# En premier : configure l'environnement de test (DATABASE_URL, SECRET_KEY…).
from tests.test_webhook import _creer_expediteur, session, _stub_signature  # noqa: F401 — fixtures
from app.services import soldes  # noqa: E402


@pytest.fixture(autouse=True)
def _pas_de_sms(monkeypatch):
    """Aucun SMS réel pendant les tests."""
    from app.services.sms_service import sms_service

    envoyes = []

    async def faux_sms(**kwargs):
        envoyes.append(kwargs)
        return True

    monkeypatch.setattr(sms_service, "envoyer_sms_retour", faux_sms)
    return envoyes


# ── Règles pures ─────────────────────────────────────────────────────────────

class TestReglesPures:
    def test_frais_retour_moitie_du_prix(self):
        assert soldes.frais_retour(10_000, 0.5) == 5_000
        assert soldes.frais_retour(13_900, 0.5) == 6_950
        assert soldes.frais_retour(0, 0.5) == 0

    def test_prelevement_partiel_jamais_sous_zero(self):
        assert soldes.credit_prelever_partiel(10_000, 5_000) == (5_000, 0)
        assert soldes.credit_prelever_partiel(1_200, 5_000) == (1_200, 3_800)
        assert soldes.credit_prelever_partiel(0, 5_000) == (0, 5_000)


# ── Parcours complet ─────────────────────────────────────────────────────────

async def _livreur(session, lat=None, lon=None):
    from app.models.livreur import Livreur
    from app.models.user import User, UserRole
    u = User(id=uuid.uuid4(), phone=f"+224602{uuid.uuid4().int % 1000000:06d}",
             role=UserRole.LIVREUR, is_verified=True)
    session.add(u)
    await session.flush()
    liv = Livreur(id=uuid.uuid4(), user_id=u.id, nom_complet="Barry", is_verified=True,
                  is_disponible=True, is_en_course=True, solde_disponible=0.0, total_gains=0.0,
                  latitude=lat, longitude=lon)
    session.add(liv)
    await session.commit()
    return u, liv


async def _course_en_livraison(session, credit=50_000):
    """Course cash (prix plancher 10 000 → livreur 8 800, commission 1 200) en
    livraison chez un livreur."""
    from app.api.v1.endpoints.courses import create_course
    from app.models.course import CourseStatus, ModePaiement
    from app.schemas.course import CourseCreate
    user, p = await _creer_expediteur(session, credit=credit)
    course = await create_course(CourseCreate(
        contact_client_nom="Client", contact_client_telephone="620000000",
        prix_propose=1, mode_paiement=ModePaiement.CASH, exige_code_livraison=True,
    ), p, session)
    _, liv = await _livreur(session)
    course.livreur_id, course.status = liv.id, CourseStatus.EN_LIVRAISON
    await session.commit()
    return user, p, course, liv


def _echec(raison):
    from app.schemas.course import EchecLivraison
    return EchecLivraison(raison=raison)


class TestEchecLivraison:
    async def test_client_absent_exige_l_arrivee_puis_l_attente(self, session):
        from app.api.v1.endpoints.courses import declarer_echec_livraison, signaler_arrivee_client
        _, _, course, liv = await _course_en_livraison(session)

        with pytest.raises(HTTPException) as exc:   # pas d'arrivée signalée
            await declarer_echec_livraison(course.id, _echec("client_absent"), liv, session)
        assert exc.value.status_code == 400

        await signaler_arrivee_client(course.id, liv, session)
        with pytest.raises(HTTPException) as exc:   # attente trop courte
            await declarer_echec_livraison(course.id, _echec("client_absent"), liv, session)
        assert "Attendez encore" in exc.value.detail

    async def test_client_absent_apres_10_min_passe_en_retour(self, session, _pas_de_sms):
        from app.api.v1.endpoints.courses import declarer_echec_livraison
        from app.models.course import CourseStatus
        _, p, course, liv = await _course_en_livraison(session)
        course.arrivee_client_at = datetime.now(timezone.utc) - timedelta(minutes=11)
        await session.commit()

        await declarer_echec_livraison(course.id, _echec("client_absent"), liv, session)
        await session.refresh(course)
        await session.refresh(liv)
        assert course.status == CourseStatus.RETOUR
        assert course.echec_livraison_raison == "client_absent"
        # Cash : la part livreur a été remise en espèces → rien sur les Gains,
        # mais elle compte dans le total gagné (trajet aller dû).
        assert liv.solde_disponible == 0
        assert liv.total_gains == course.montant_livreur
        assert len(_pas_de_sms) == 1   # client prévenu

    async def test_refus_du_colis_immediat(self, session):
        from app.api.v1.endpoints.courses import declarer_echec_livraison
        from app.models.course import CourseStatus
        _, _, course, liv = await _course_en_livraison(session)
        await declarer_echec_livraison(course.id, _echec("refus_client"), liv, session)
        await session.refresh(course)
        assert course.status == CourseStatus.RETOUR

    async def test_refuse_si_livreur_loin_du_client(self, session):
        from app.api.v1.endpoints.courses import declarer_echec_livraison
        _, _, course, liv = await _course_en_livraison(session)
        course.latitude_client, course.longitude_client = 9.60, -13.60
        liv.latitude, liv.longitude = 9.50, -13.70     # ~15 km
        await session.commit()
        with pytest.raises(HTTPException) as exc:
            await declarer_echec_livraison(course.id, _echec("refus_client"), liv, session)
        assert "Rendez-vous sur place" in exc.value.detail

    async def test_mobile_money_part_livreur_creditee_a_l_echec(self, session):
        from app.api.v1.endpoints.courses import declarer_echec_livraison
        from app.models.course import ModePaiement
        _, _, course, liv = await _course_en_livraison(session)
        course.mode_paiement, course.paiement_confirme = ModePaiement.MOBILE_MONEY, "oui"
        await session.commit()
        await declarer_echec_livraison(course.id, _echec("refus_client"), liv, session)
        await session.refresh(liv)
        assert liv.solde_disponible == course.montant_livreur

    async def test_plus_d_annulation_une_fois_en_retour(self, session):
        from app.api.v1.endpoints.courses import annuler_course, declarer_echec_livraison
        from app.schemas.course import CourseAnnulation
        user, _, course, liv = await _course_en_livraison(session)
        await declarer_echec_livraison(course.id, _echec("refus_client"), liv, session)
        with pytest.raises(HTTPException) as exc:
            await annuler_course(course.id, CourseAnnulation(raison="test"), user, session)
        assert exc.value.status_code == 400


class TestRetourRecu:
    async def test_frais_de_retour_verses_au_livreur(self, session):
        from app.api.v1.endpoints.courses import confirmer_retour_recu, declarer_echec_livraison
        from app.models.course import CourseStatus
        from app.services import credit_service
        _, p, course, liv = await _course_en_livraison(session, credit=50_000)
        await declarer_echec_livraison(course.id, _echec("refus_client"), liv, session)

        await confirmer_retour_recu(course.id, p, session)
        await session.refresh(course)
        await session.refresh(liv)
        assert course.status == CourseStatus.RETOURNEE
        assert course.frais_retour == 5_000 and course.frais_retour_restant == 0
        assert liv.solde_disponible == 5_000
        assert liv.is_en_course is False
        # 50 000 − commission 1 200 (non rendue : service rendu) − frais 5 000
        assert await credit_service.credit_disponible(session, p.id) == 43_800

    async def test_retour_recu_idempotent(self, session):
        from app.api.v1.endpoints.courses import confirmer_retour_recu, declarer_echec_livraison
        _, p, course, liv = await _course_en_livraison(session)
        await declarer_echec_livraison(course.id, _echec("refus_client"), liv, session)
        await confirmer_retour_recu(course.id, p, session)
        with pytest.raises(HTTPException):
            await confirmer_retour_recu(course.id, p, session)
        await session.refresh(liv)
        assert liv.solde_disponible == 5_000   # versé une seule fois

    async def test_credit_insuffisant_bloque_les_courses_jusqu_a_la_recharge(self, session):
        from app.api.v1.endpoints.courses import (
            confirmer_retour_recu, create_course, declarer_echec_livraison,
        )
        from app.models.course import ModePaiement
        from app.schemas.course import CourseCreate
        from app.services import credit_service
        # Crédit = juste la commission → 0 après création de la course.
        _, p, course, liv = await _course_en_livraison(session, credit=1_200)
        await declarer_echec_livraison(course.id, _echec("refus_client"), liv, session)
        await confirmer_retour_recu(course.id, p, session)
        await session.refresh(course)
        assert course.frais_retour_restant == 5_000
        assert await credit_service.frais_retour_dus(session, p.id) == 5_000
        from app.api.v1.endpoints.credit import get_credit
        assert (await get_credit(p, session))["frais_retour_dus"] == 5_000   # visible dans l'app

        nouvelle = CourseCreate(contact_client_nom="C2", contact_client_telephone="620000001",
                                prix_propose=1, mode_paiement=ModePaiement.CASH)
        with pytest.raises(HTTPException) as exc:
            await create_course(nouvelle, p, session)
        assert "Frais de retour impayés" in exc.value.detail

        # La recharge verse d'abord le reste dû au livreur.
        await credit_service.recharger(session, p.id, 10_000)
        await session.refresh(liv)
        await session.refresh(course)
        assert liv.solde_disponible == 5_000
        assert course.frais_retour_restant == 0
        assert await credit_service.credit_disponible(session, p.id) == 5_000
        await create_course(nouvelle, p, session)   # débloqué


class TestSurveillanceRetour:
    async def test_colis_non_recupere_alerte_admin(self, session):
        from app.api.v1.endpoints.courses import declarer_echec_livraison
        from app.services.expiration_service import surveiller_courses_en_cours
        _, _, course, liv = await _course_en_livraison(session)
        await declarer_echec_livraison(course.id, _echec("refus_client"), liv, session)
        course.echec_livraison_at = datetime.now(timezone.utc) - timedelta(hours=3)
        await session.commit()
        res = await surveiller_courses_en_cours(session)
        assert res["alertes"] >= 1
