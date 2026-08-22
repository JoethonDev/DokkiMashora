"""Template tags for UI helpers."""

from django import template
from django.utils.translation import gettext

register = template.Library()


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
