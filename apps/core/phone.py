"""Canonical phone normalization shared across all apps.

One normalizer for storage and matching (E.164 style, ``+201...``). Every
write and every identifier match uses this single function so stored values
are directly comparable.
"""

from __future__ import annotations

import re


def normalize_phone_number(value) -> str:
    """Return the canonical E.164-style phone, or an empty string.

    Strips formatting, converts a leading ``00`` to ``+``, and expands a bare
    Egyptian ``0``-prefixed 11-digit mobile to ``+2...``.
    """
    cleaned = re.sub(r"[^\d+]", "", (value or "").strip())
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if cleaned.startswith("0") and not cleaned.startswith("+") and len(cleaned) == 11:
        cleaned = "+2" + cleaned
    return cleaned