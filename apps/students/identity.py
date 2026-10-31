"""Country-aware identity helpers for registration, admin edit, and payment matching.

Egypt uses National ID (14 digits). Non-Egypt uses Passport ID (alphanumeric).
The stored DB column is still named ``national_id``; the business concept is
``identity_number``.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User
from apps.core.phone import validate_phone
from apps.students.models import StudentProfile

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


def country_code_field_choices(current_value: str = ""):
    """Return two-character choices, retaining an existing legacy value."""
    normalized = {}
    for code, label in COUNTRY_CHOICES:
        normalized.setdefault(normalize_country_code(code), label)
    choices = [("", _("Select country")), *normalized.items()]
    existing = (current_value or "").strip().upper()
    if existing and existing not in normalized:
        choices.append((existing, _("Existing country code: %(code)s") % {"code": existing}))
    return choices


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


def validate_unique_identity_values(
    *,
    country_code: str,
    phone: str = "",
    identity_number: str = "",
    user_id: int | None = None,
    profile_id: int | None = None,
) -> tuple[str, str]:
    """Normalize and validate identity values across both owner tables.

    Phone and identity numbers are authoritative on different models for
    different account types, so model-local unique constraints are not enough
    to protect the product invariant. The caller still needs the shared
    transaction lock for race-time writes; this helper owns the friendly
    cross-table validation and normalization.
    """
    normalized_phone = validate_phone(phone) if phone else ""
    normalized_identity = validate_identity_number(country_code, identity_number) if identity_number else ""
    if normalized_phone:
        if (
            StudentProfile.objects.exclude(pk=profile_id).filter(phone=normalized_phone).exists()
            or User.objects.exclude(pk=user_id).exclude(phone="").filter(phone=normalized_phone).exists()
        ):
            raise ValidationError(_("This phone number is already in use."))
    if normalized_identity:
        if (
            StudentProfile.objects.exclude(pk=profile_id)
            .filter(national_id__iexact=normalized_identity)
            .exists()
            or User.objects.exclude(pk=user_id)
            .filter(national_id__iexact=normalized_identity)
            .exists()
        ):
            raise ValidationError(_("This identity number is already in use."))
    return normalized_phone, normalized_identity
