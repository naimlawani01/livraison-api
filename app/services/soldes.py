"""Règles des soldes Sönaiyaa — Crédit (Expéditeur) & Gains (Livreur).

Source de vérité des **décisions d'argent**, en fonctions pures et déterministes
(aucune dépendance DB) — le service transactionnel n'en est qu'un mince emballage
avec verrou. C'est le cœur du filet de sécurité : ici on garantit qu'un solde ne
part jamais dans le mauvais sens.

Deux soldes, deux sens :

* **Crédit** (Expéditeur) — prépayé, se **dépense**. On le recharge (PSP entrant),
  on en débite la **commission** à chaque course. Ne peut **pas** passer sous zéro :
  si le Crédit ne couvre pas la commission, la course n'est pas créée.

* **Gains** (Livreur) — s'**accumulent**. Crédités par les courses réglées via la
  plateforme et les indemnités, **retirés** vers Mobile Money (PSP sortant).
  Toujours **positifs** : le livreur ne met jamais d'argent, il ne fait que retirer.
"""
from __future__ import annotations

# Montants planchers (GNF)
MONTANT_MIN_RECHARGE: int = 5_000
MONTANT_MIN_RETRAIT: int = 5_000


class MontantInvalide(ValueError):
    """Montant nul, négatif, ou sous le plancher autorisé."""


class SoldeInsuffisant(ValueError):
    """L'opération ferait passer un solde sous zéro."""


def _exiger_positif(montant: float) -> None:
    if montant <= 0:
        raise MontantInvalide("Le montant doit être strictement positif.")


def _arrondir(montant: float) -> float:
    return round(montant, 2)


# ── Crédit (Expéditeur) — se dépense, jamais négatif ─────────────────────────

def credit_couvre(solde: float, commission: float) -> bool:
    """Le Crédit couvre-t-il cette commission ? (garde-fou de création de course)."""
    return solde >= commission


def credit_debiter(solde: float, commission: float) -> float:
    """Débite la commission du Crédit. Refuse si le solde ne couvre pas."""
    _exiger_positif(commission)
    if solde < commission:
        raise SoldeInsuffisant(
            f"Crédit insuffisant : {solde} < commission {commission}."
        )
    return _arrondir(solde - commission)


def credit_recharger(solde: float, montant: float) -> float:
    """Recharge le Crédit (PSP entrant). Applique le plancher de recharge."""
    _exiger_positif(montant)
    if montant < MONTANT_MIN_RECHARGE:
        raise MontantInvalide(
            f"Recharge minimum : {MONTANT_MIN_RECHARGE} GNF."
        )
    return _arrondir(solde + montant)


# ── Gains (Livreur) — s'accumulent, toujours positifs ────────────────────────

def gains_crediter(gains: float, montant: float) -> float:
    """Crédite un gain (course plateforme, indemnité)."""
    _exiger_positif(montant)
    return _arrondir(gains + montant)


def gains_peut_retirer(gains: float, montant: float) -> bool:
    """Le livreur peut-il retirer ce montant ?"""
    return montant > 0 and gains >= montant


def gains_retirer(gains: float, montant: float) -> float:
    """Retire un montant des Gains (PSP sortant). Applique le plancher de retrait."""
    _exiger_positif(montant)
    if montant < MONTANT_MIN_RETRAIT:
        raise MontantInvalide(
            f"Retrait minimum : {MONTANT_MIN_RETRAIT} GNF."
        )
    if gains < montant:
        raise SoldeInsuffisant(
            f"Gains insuffisants : {gains} < retrait {montant}."
        )
    return _arrondir(gains - montant)
