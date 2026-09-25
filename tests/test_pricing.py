"""Tests du chemin de l'argent — tarification des courses.

Première brique du filet de sécurité : la tarification est pure et déterministe,
c'est le socle sur lequel reposent commission (Crédit) et gain livreur (Gains).
"""
import pytest

from app.services.pricing import (
    calculer_tarif,
    multiplicateur_colis,
    PRIX_BASE,
    TAUX_COMMISSION,
)


class TestPlancher:
    def test_distance_zero_est_le_plancher(self):
        t = calculer_tarif(0)
        assert t.prix == 10_000
        assert t.commission == 1_200      # 12 %
        assert t.gain_livreur == 8_800

    def test_course_tres_courte_ne_descend_pas_sous_le_plancher(self):
        assert calculer_tarif(0.1).prix == PRIX_BASE


class TestGrilleDistance:
    @pytest.mark.parametrize("km, prix, commission, gain", [
        (2, 13_000, 1_560, 11_440),
        (5, 17_500, 2_100, 15_400),
        (15, 32_500, 3_900, 28_600),
    ])
    def test_grille_de_reference(self, km, prix, commission, gain):
        t = calculer_tarif(km)
        assert t.prix == prix
        assert t.commission == commission
        assert t.gain_livreur == gain


class TestInvariants:
    @pytest.mark.parametrize("km", [0, 1, 2.4, 5, 7.7, 15, 30])
    def test_commission_plus_gain_egale_prix(self, km):
        t = calculer_tarif(km)
        assert t.commission + t.gain_livreur == t.prix

    @pytest.mark.parametrize("km", [0, 1, 3.3, 8, 12.5])
    def test_prix_toujours_multiple_de_500(self, km):
        assert calculer_tarif(km).prix % 500 == 0

    def test_commission_est_bien_12_pourcent(self):
        t = calculer_tarif(5)
        assert t.commission == round(t.prix * TAUX_COMMISSION)


class TestTypeColis:
    def test_fragile_majore_de_20pct(self):
        # 5 km standard = 17 500 ; ×1.2 = 21 000
        assert calculer_tarif(5, "fragile").prix == 21_000

    def test_volumineux_majore_de_40pct(self):
        # 2 km standard = 13 000 ; ×1.4 = 18 200 → arrondi 500 = 18 000
        assert calculer_tarif(2, "volumineux").prix == 18_000

    def test_type_inconnu_retombe_sur_standard(self):
        assert multiplicateur_colis("bijou") == 1.0
        assert calculer_tarif(5, "documents").prix == calculer_tarif(5, "standard").prix
        assert calculer_tarif(5, "alimentaire").prix == calculer_tarif(5, "standard").prix

    def test_casse_et_espaces_ignores(self):
        assert multiplicateur_colis("  FRAGILE ") == 1.2

    def test_type_none_retombe_sur_standard(self):
        assert multiplicateur_colis(None) == 1.0


class TestGarde:
    def test_distance_negative_rejetee(self):
        with pytest.raises(ValueError):
            calculer_tarif(-1)
