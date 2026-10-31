"""Centralized date parsing utility (DUP-006)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.utils import timezone


def aware_date_bounds(date_from: str | None, date_to: str | None):
    """Return an inclusive-exclusive aware datetime range for ISO date inputs."""
    start = None
    end = None
    if date_from:
        try:
            start = timezone.make_aware(datetime.combine(date.fromisoformat(date_from), time.min))
        except ValueError:
            start = None
    if date_to:
        try:
            end = timezone.make_aware(
                datetime.combine(
                    date.fromisoformat(date_to) + timedelta(days=1),
                    time.min,
                )
            )
        except ValueError:
            end = None
    return start, end


def parse_date(value: str) -> datetime | None:
    """Parse a date string in common CSV formats and return timezone-aware datetime."""
    if not value or not str(value).strip():
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).replace(tzinfo=timezone.get_current_timezone())
        except ValueError:
            continue
    return None
