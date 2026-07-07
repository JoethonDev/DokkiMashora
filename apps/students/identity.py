"""Country-aware identity helpers for registration, admin edit, and payment matching.

Egypt uses National ID (14 digits). Non-Egypt uses Passport ID (alphanumeric).
The stored DB column is still named ``national_id``; the business concept is
``identity_number``.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

COUNTRY_EG = "EG"
COUNTRY_CODE_LENGTH = 2

COUNTRY_CHOICES = [
    ("EG", _("Egypt")),
    ("SA", _("Saudi Arabia")),
    ("AE", _("United Arab Emirates")),
    ("US", _("United States")),
    ("GB", _("United Kingdom")),
    ("CA", _("Canada")),
    ("AU", _("Australia")),
    ("JO", _("Jordan")),
    ("LB", _("Lebanon")),
    ("IQ", _("Iraq")),
    ("SY", _("Syria")),
    ("PS", _("Palestine")),
    ("LY", _("Libya")),
    ("SD", _("Sudan")),
    ("TN", _("Tunisia")),
    ("MA", _("Morocco")),
    ("DZ", _("Algeria")),
    ("OTHER", _("Other")),
]


def normalize_country_code(value: str) -> str:
    """Normalize to uppercase ISO two-letter code; default to EG on empty."""
    stripped = value.strip() if value else ""
    if not stripped:
        return COUNTRY_EG
    # ponytail: truncation is intentional; production choices bounded by form ChoiceField
    return stripped.upper()[:COUNTRY_CODE_LENGTH]


def is_egypt(country_code: str) -> bool:
    return normalize_country_code(country_code) == COUNTRY_EG


def identity_label(country_code: str) -> str:
    if is_egypt(country_code):
        return _("National ID")
    return _("Passport ID")


def normalize_identity_number(country_code: str, value: str) -> str:
    """Normalize an identity value for storage and matching."""
    if is_egypt(country_code):
        return re.sub(r"\s+", "", value).strip()
    return re.sub(r"\s+", "", value).strip().upper()


def validate_identity_number(country_code: str, value: str) -> str:
    """Validate and return the normalized identity number.

    Raises ``ValidationError`` when the value does not meet the
    country-specific requirements.
    """
    normalized = normalize_identity_number(country_code, value)
    if not normalized:
        label = identity_label(country_code)
        raise ValidationError(
            _("%(label)s is required.") % {"label": label}
        )
    if is_egypt(country_code):
        if not normalized.isdigit() or len(normalized) != 14:
            raise ValidationError(
                _("Egyptian National ID must be exactly 14 digits.")
            )
    else:
        if not re.match(r"^[A-Z0-9\-]+$", normalized):
            raise ValidationError(
                _("Passport ID contains invalid characters.")
            )
    return normalized
