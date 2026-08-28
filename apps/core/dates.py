"""Centralized date parsing utility (DUP-006)."""

from __future__ import annotations

from datetime import datetime

from django.utils import timezone


def parse_date(value: str) -> datetime | None:
    """Parse a date string in common CSV formats and return timezone-aware datetime."""
    if not value or not str(value).strip():
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).replace(
                tzinfo=timezone.get_current_timezone()
            )
        except ValueError:
            continue
    return None
