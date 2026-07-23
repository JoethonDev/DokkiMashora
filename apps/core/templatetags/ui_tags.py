"""Template tags for UI helpers (datetime formatting, YouTube embed, etc.)."""

import re
from datetime import date as date_type

from django import template
from django.conf import settings
from django.utils.safestring import mark_safe
from django.template.defaultfilters import date as date_filter

register = template.Library()


@register.simple_tag(takes_context=True)
def nav_attrs(context, url_name, base_class=""):
    current = getattr(context.get("request").resolver_match, "view_name", "")
    active = current == url_name
    attrs = f'class="{base_class}{" dm-nav-active" if active else ""}"'
    if active:
        attrs += ' aria-current="page"'
    return mark_safe(attrs)


@register.simple_tag
def dt(value, fmt=None):
    if not value:
        return ""
    if fmt:
        return date_filter(value, fmt)
    default_fmt = settings.SHORT_DATE_FORMAT if isinstance(value, date_type) else settings.SHORT_DATETIME_FORMAT
    return date_filter(value, default_fmt)


_YOUTUBE_RE = re.compile(
    r"(?:youtube\.com/watch\?(?:.*&)?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/shorts/|youtube\.com/live/)([\w-]+)"
)


@register.filter
def youtube_embed(url):
    """Convert a YouTube watch / short / live URL to an embed URL."""
    if not url:
        return ""
    match = _YOUTUBE_RE.search(url)
    if match:
        return f"https://www.youtube.com/embed/{match.group(1)}?rel=0"
    return url


@register.filter
def startswith(value, arg):
    return value.startswith(arg) if value and arg else False


@register.filter
def dict_key(d, key):
    """Look up a dictionary key (supports integer keys)."""
    return d.get(key)


_SUCCESS = frozenset({"active", "accepted", "activated", "paid", "passed", "completed", "sent", "matched", "published", "graded", "present"})
_INFO = frozenset({"submitted", "under_review", "in_progress", "scheduled", "open", "processing", "assigned", "invited"})
_WARNING = frozenset({"pending", "payment_pending", "documents_requested", "previewed", "ungraded", "late", "restricted", "unmatched", "absent"})
_DANGER = frozenset({"rejected", "failed", "error", "overdue", "suspended", "violated", "revoked"})


@register.filter
def status_tone(code):
    """Map a raw status code to a tone class: success/info/warning/danger/neutral."""
    if not code:
        return "neutral"
    code = str(code).lower().replace(" ", "_")
    if code in _SUCCESS:
        return "green"
    if code in _INFO:
        return "blue"
    if code in _WARNING:
        return "amber"
    if code in _DANGER:
        return "red"
    return "neutral"
