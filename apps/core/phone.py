"""Canonical phone normalization shared across all apps.

One normalizer for storage and matching (E.164 style, ``+201...``). Every
write and every identifier match uses this single function so stored values
are directly comparable.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

try:
    import phonenumbers  # type: ignore
except ImportError:  # pragma: no cover
    phonenumbers = None  # type: ignore


def normalize_phone(value: str | None) -> str:
    """Return the canonical E.164-style phone, or an empty string.

    Handles Egyptian variants: ``010...``, ``2010...``, ``+2010...``,
    ``002010...``. Strips formatting, converts leading ``00`` to ``+``,
    expands bare ``0``-prefixed 11-digit mobile to ``+20...``, and prefixes
    ``+`` for bare ``20...``/``2...``. Uses ``phonenumbers`` when available
    for robust E.164 formatting.
    """
    if not value:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    cleaned = re.sub(r"[^\d+]", "", raw)
    if not cleaned:
        return ""
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    elif cleaned.startswith("0") and not cleaned.startswith("+"):
        if len(cleaned) == 11:
            cleaned = "+2" + cleaned
    elif cleaned and not cleaned.startswith("+"):
        if cleaned.startswith("20") or cleaned.startswith("2"):
            cleaned = "+" + cleaned

    if phonenumbers is not None:
        try:
            if cleaned.startswith("+"):
                parsed = phonenumbers.parse(cleaned, None)
            else:
                parsed = phonenumbers.parse(cleaned, "EG")
            if phonenumbers.is_possible_number(parsed):
                return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
            # Fallback: if parse succeeded and digits >= 7, still format
            digits = re.sub(r"\D", "", cleaned)
            if len(digits) >= 7:
                try:
                    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
                except Exception:
                    pass
        except Exception:
            pass

    if cleaned and not cleaned.startswith("+") and cleaned.startswith("20"):
        cleaned = "+" + cleaned
    return cleaned


# Backwards-compat alias — single definition, not a second ``def``.
normalize_phone_number = normalize_phone


def validate_phone(value: str | None) -> str:
    """Validate and return the normalized phone, or raise ValidationError.

    Uses :func:`normalize_phone` and checks E.164 length (7–15 digits).
    """
    normalized = normalize_phone(value)
    if not normalized:
        raise ValidationError(_("Enter a valid mobile number."))
    digits = re.sub(r"\D", "", normalized)
    if len(digits) < 7 or len(digits) > 15:
        raise ValidationError(_("Enter a valid mobile number."))
    if not normalized.startswith("+"):
        raise ValidationError(_("Enter a valid mobile number."))
    if phonenumbers is not None:
        try:
            parsed = phonenumbers.parse(normalized, None)
            if not phonenumbers.is_possible_number(parsed):
                # Keep len check as primary; possible check is advisory
                pass
        except Exception as exc:
            raise ValidationError(_("Enter a valid mobile number.")) from exc
    return normalized
