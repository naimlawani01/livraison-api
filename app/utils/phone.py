"""Helpers de validation/normalisation des numéros de téléphone guinéens.

Format canonique stocké en base : ``+224XXXXXXXXX`` (12 caractères),
où les 9 chiffres locaux commencent par ``6`` (mobiles Orange/MTN/Cellcom).
"""
import re

GUINEA_PHONE_RE = re.compile(r"^\+2246\d{8}$")


class InvalidGuineaPhoneError(ValueError):
    """Levée quand un numéro ne respecte pas le format guinéen."""


def normalize_guinea_phone(value: str) -> str:
    """Normalise un numéro vers ``+224XXXXXXXXX``.

    Accepte ``626947150``, ``+224626947150``, ``00224 626 947 150``, etc.
    Rejette tout numéro dont la partie locale ne commence pas par ``6``.
    """
    if not isinstance(value, str):
        raise InvalidGuineaPhoneError("Numéro de téléphone requis")

    digits = re.sub(r"\D", "", value)
    if digits.startswith("00224"):
        local = digits[5:]
    elif digits.startswith("224"):
        local = digits[3:]
    else:
        local = digits

    if not re.fullmatch(r"6\d{8}", local):
        raise InvalidGuineaPhoneError(
            "Numéro guinéen invalide (9 chiffres, commence par 6)"
        )
    return f"+224{local}"


def is_valid_guinea_phone(value: str) -> bool:
    """Vrai si la chaîne est déjà au format ``+224XXXXXXXXX`` valide."""
    return bool(GUINEA_PHONE_RE.fullmatch(value or ""))
