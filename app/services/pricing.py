"""Tarification des courses Sönaiyaa — source de vérité du modèle de prix.

Le prix d'une course est fonction de la distance (point de retrait de
l'expéditeur → quartier de destination). La plateforme prélève une commission ;
le reste revient au livreur.

    prix         = arrondi(PRIX_BASE + PRIX_KM × distance_km) × mult_colis
    commission   = prix × TAUX_COMMISSION      (débitée du Crédit de l'expéditeur)
    gain_livreur = prix − commission           (réglé au livreur)

Invariant garanti : ``commission + gain_livreur == prix``.

Ce module est volontairement **pur** (aucune dépendance DB ni settings) afin de
rester déterministe et testable sans environnement. Il remplace l'ancienne
formule dispersée dans ``endpoints/courses.py`` (commission 15 %, surge
horaire, 5 catégories de colis) que les endpoints migreront pour consommer ici.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# ── Paramètres du modèle tarifaire ───────────────────────────────────────────
PRIX_BASE: int = 10_000        # GNF — plancher garanti pour toute course
PRIX_KM: int = 1_500           # GNF ajoutés par kilomètre de distance
ARRONDI: int = 500             # le prix est arrondi au multiple de 500 GNF le plus proche
TAUX_COMMISSION: float = 0.12  # 12 % — part plateforme prélevée sur le prix

# Multiplicateurs par type de colis (modèle simplifié : 3 catégories).
# « alimentaire » et « documents » de l'ancienne grille retombent sur standard.
MULT_COLIS: dict = {
    "standard": 1.0,
    "fragile": 1.2,
    "volumineux": 1.4,
}
MULT_COLIS_DEFAUT: float = 1.0


@dataclass(frozen=True)
class Tarif:
    """Décomposition financière d'une course.

    Invariant : ``commission + gain_livreur == prix``.
    """
    prix: int             # ce que paie l'expéditeur (total, ferme)
    commission: int       # part plateforme, débitée du Crédit de l'expéditeur
    gain_livreur: int     # part livreur (réglée en cash ou via la plateforme)
    distance_km: float
    type_colis: str
    mult_colis: float


def multiplicateur_colis(type_colis: Optional[str]) -> float:
    """Multiplicateur du type de colis ; retombe sur standard si inconnu."""
    return MULT_COLIS.get((type_colis or "").strip().lower(), MULT_COLIS_DEFAUT)


def _arrondir(montant: float) -> int:
    """Arrondit au multiple de ``ARRONDI`` GNF le plus proche."""
    return int(round(montant / ARRONDI) * ARRONDI)


def calculer_tarif(distance_km: float, type_colis: str = "standard") -> Tarif:
    """Calcule le tarif d'une course.

    :param distance_km: distance retrait → livraison en km (``>= 0``).
    :param type_colis: ``standard`` | ``fragile`` | ``volumineux`` (autre → standard).
    :raises ValueError: si ``distance_km`` est négatif.
    """
    if distance_km < 0:
        raise ValueError("distance_km doit être >= 0")

    mult = multiplicateur_colis(type_colis)
    brut = (PRIX_BASE + distance_km * PRIX_KM) * mult
    prix = max(PRIX_BASE, _arrondir(brut))

    commission = int(round(prix * TAUX_COMMISSION))
    gain_livreur = prix - commission

    return Tarif(
        prix=prix,
        commission=commission,
        gain_livreur=gain_livreur,
        distance_km=round(distance_km, 2),
        type_colis=(type_colis or "standard").strip().lower(),
        mult_colis=mult,
    )
