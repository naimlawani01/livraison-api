"""Tests du chemin de l'argent — règles des soldes Crédit & Gains.

On verrouille ici les garanties qui protègent l'argent réel :
le Crédit ne passe jamais sous zéro, les Gains restent toujours positifs.
"""
import pytest

from app.services.soldes import (
    credit_couvre,
    credit_debiter,
    credit_recharger,
    gains_crediter,
    gains_peut_retirer,
    gains_retirer,
    MontantInvalide,
    SoldeInsuffisant,
    MONTANT_MIN_RECHARGE,
    MONTANT_MIN_RETRAIT,
)


# ── Crédit (Expéditeur) ──────────────────────────────────────────────────────

class TestCreditDebit:
    def test_debit_normal(self):
        assert credit_debiter(50_000, 1_200) == 48_800

    def test_debit_pile_le_solde(self):
        assert credit_debiter(1_200, 1_200) == 0

    def test_debit_au_dela_du_solde_refuse(self):
        with pytest.raises(SoldeInsuffisant):
            credit_debiter(1_000, 1_200)

    def test_le_credit_ne_passe_jamais_sous_zero(self):
        # même à 1 GNF près, on refuse plutôt que d'aller en négatif
        with pytest.raises(SoldeInsuffisant):
            credit_debiter(1_199, 1_200)

    def test_commission_nulle_ou_negative_refusee(self):
        with pytest.raises(MontantInvalide):
            credit_debiter(50_000, 0)
        with pytest.raises(MontantInvalide):
            credit_debiter(50_000, -100)


class TestCreditCouvre:
    def test_couvre_vrai_et_faux(self):
        assert credit_couvre(2_000, 1_200) is True
        assert credit_couvre(1_200, 1_200) is True
        assert credit_couvre(1_000, 1_200) is False


class TestCreditRecharge:
    def test_recharge_normale(self):
        assert credit_recharger(48_800, 50_000) == 98_800

    def test_recharge_sous_le_plancher_refusee(self):
        with pytest.raises(MontantInvalide):
            credit_recharger(0, MONTANT_MIN_RECHARGE - 1)

    def test_recharge_pile_au_plancher_acceptee(self):
        assert credit_recharger(0, MONTANT_MIN_RECHARGE) == MONTANT_MIN_RECHARGE


# ── Gains (Livreur) ──────────────────────────────────────────────────────────

class TestGainsCredit:
    def test_credit_accumule(self):
        assert gains_crediter(10_000, 12_320) == 22_320

    def test_credit_negatif_refuse(self):
        with pytest.raises(MontantInvalide):
            gains_crediter(10_000, -5)


class TestGainsRetrait:
    def test_retrait_normal(self):
        assert gains_retirer(34_500, 20_000) == 14_500

    def test_retrait_de_tout_le_solde(self):
        assert gains_retirer(20_000, 20_000) == 0

    def test_retrait_au_dela_refuse(self):
        with pytest.raises(SoldeInsuffisant):
            gains_retirer(14_500, 20_000)

    def test_les_gains_ne_deviennent_jamais_negatifs(self):
        with pytest.raises(SoldeInsuffisant):
            gains_retirer(19_999, 20_000)

    def test_retrait_sous_le_plancher_refuse(self):
        with pytest.raises(MontantInvalide):
            gains_retirer(50_000, MONTANT_MIN_RETRAIT - 1)

    def test_peut_retirer(self):
        assert gains_peut_retirer(20_000, 20_000) is True
        assert gains_peut_retirer(19_999, 20_000) is False
        assert gains_peut_retirer(20_000, 0) is False


# ── Scénarios de bout en bout (round-trips) ──────────────────────────────────

class TestScenarios:
    def test_cycle_credit_recharge_puis_plusieurs_commissions(self):
        solde = credit_recharger(0, 50_000)          # 50 000
        solde = credit_debiter(solde, 1_200)         # course plancher
        solde = credit_debiter(solde, 2_016)         # course fragile
        solde = credit_debiter(solde, 1_680)         # course moyenne
        assert solde == 45_104

    def test_cycle_gains_plusieurs_courses_puis_retrait(self):
        gains = gains_crediter(0, 12_320)
        gains = gains_crediter(gains, 3_000)         # indemnité
        gains = gains_crediter(gains, 8_800)
        assert gains == 24_120
        gains = gains_retirer(gains, 20_000)
        assert gains == 4_120
