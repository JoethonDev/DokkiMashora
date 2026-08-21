"""Template tags for UI helpers (datetime formatting, YouTube embed, etc.)."""

import re
from datetime import date as date_type

from django import template
from django.conf import settings
from django.template.defaultfilters import date as date_filter
from django.utils.safestring import mark_safe
from django.utils.translation import gettext

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


_SUCCESS = frozenset({"active", "accepted", "activated", "paid", "passed", "completed", "released", "sent", "matched", "published", "graded", "present", "pass"})
_INFO = frozenset({"submitted", "under_review", "in_progress", "scheduled", "open", "processing", "assigned", "invited", "ready", "continue", "interview_requested", "entrance_submitted"})
_WARNING = frozenset({"pending", "payment_pending", "documents_requested", "previewed", "ungraded", "late", "restricted", "unmatched", "absent", "awaiting_release"})
_DANGER = frozenset({"rejected", "failed", "error", "overdue", "suspended", "violated", "revoked", "max_attempts", "fail"})

# Non-color-only semantic glyphs per tone (P1-02). Direction-neutral, render
# identically in RTL/LTR, and remain understandable without color.
_ICON_BY_TONE = {"success": "✓", "warning": "!", "danger": "✕", "info": "i", "neutral": "•"}


@register.filter
def status_tone(code):
    """Map a raw status code to a tone class: success/info/warning/danger/neutral."""
    if not code:
        return "neutral"
    code = str(code).lower().replace(" ", "_").replace("-", "_")
    if code in _SUCCESS:
        return "success"
    if code in _INFO:
        return "info"
    if code in _WARNING:
        return "warning"
    if code in _DANGER:
        return "danger"
    return "neutral"


@register.filter
def status_icon(code):
    """Semantic glyph for a status code (non-color-only indication).

    The glyph is decorative redundancy: the translated label always carries
    the meaning, so it is safe to hide from assistive technology.
    """
    return _ICON_BY_TONE.get(status_tone(code), "•")


@register.filter
def boolean_label(value, labels="Yes,No"):
    """Translate a boolean label without exposing Python True/False values."""
    choices = [item.strip() for item in labels.split(",")]
    index = 0 if value else 1
    if value is None and len(choices) > 2:
        index = 2
    if index >= len(choices):
        return ""
    return gettext(choices[index])
