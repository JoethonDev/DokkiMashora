"""Presentation helpers shared by transactional email rendering.

Emails are Arabic-only and render dates as ``DD-MM-YYYY (HH:MM AM/PM)
(Arabic day name)`` in the business timezone. Raw URLs are promoted to a
button plus the visible clickable URL so the recipient can always use or copy
the link.
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.translation import gettext

ARABIC_DAY_NAMES = (
    "الاثنين",
    "الثلاثاء",
    "الأربعاء",
    "الخميس",
    "الجمعة",
    "السبت",
    "الأحد",
)

#: Template context keys whose string values must be presented as dates.
EMAIL_DATE_KEYS = frozenset(
    {
        "date_of_birth",
        "due_at",
        "exam_close_at",
        "exam_open_at",
        "expires_at",
        "interview_at",
        "paid_at",
    }
)

_URL_PATTERN = re.compile(r"https?://[^\s<>\"]+")
_URL_TRAILING_PUNCTUATION = ".,;:!?)]}\"'"
_DATE_ONLY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BUSINESS_TIMEZONE = ZoneInfo(settings.TIME_ZONE)


def format_email_datetime(value: object) -> str | None:
    """Return a human-readable Arabic date/time, or None when unparseable."""
    parsed: datetime | date | None = None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if _DATE_ONLY_PATTERN.match(raw):
            parsed = parse_date(raw)
        else:
            parsed = parse_datetime(raw)
            if parsed is None:
                parsed = parse_date(raw)
    if parsed is None:
        return None

    if isinstance(parsed, datetime):
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        parsed = parsed.astimezone(_BUSINESS_TIMEZONE)
        period = "AM" if parsed.hour < 12 else "PM"
        hour = parsed.hour % 12 or 12
        return f"{parsed:%d-%m-%Y} ({hour:02d}:{parsed:%M} {period}) ({ARABIC_DAY_NAMES[parsed.weekday()]})"
    return f"{parsed:%d-%m-%Y} ({ARABIC_DAY_NAMES[parsed.weekday()]})"


def humanize_email_datetime_context(context: dict) -> dict:
    """Return a copy of the template context with date values made readable."""
    normalized = dict(context)
    for key, value in normalized.items():
        if not (key in EMAIL_DATE_KEYS or key.endswith("_at")):
            continue
        if not isinstance(value, (datetime, date, str)):
            continue
        formatted = format_email_datetime(value)
        if formatted:
            normalized[key] = formatted
    return normalized


def _link_block(url: str) -> str:
    href = html.escape(html.unescape(url), quote=True)
    label = gettext("Open")
    return (
        '<div style="margin:16px 0;padding:14px 16px;background:#f8fafc;'
        'border:1px solid #e2e8f0;border-radius:8px">'
        f'<a href="{href}" style="display:inline-block;background:#2b675f;'
        "color:#ffffff;text-decoration:none;padding:10px 18px;"
        f'border-radius:6px;font-weight:700">{label}</a>'
        '<div style="margin-top:8px;font-size:12px;line-height:1.6;'
        f'word-break:break-all"><a href="{href}" style="color:#2b675f;'
        f'text-decoration:underline">{href}</a></div></div>'
    )


def linkify_email_body(text: str) -> str:
    """Turn each raw URL in the rendered body into a button plus visible link."""

    def _replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        url = raw.rstrip(_URL_TRAILING_PUNCTUATION)
        trailing = raw[len(url) :]
        if not url:
            return raw
        return _link_block(url) + trailing

    return _URL_PATTERN.sub(_replace, text)
