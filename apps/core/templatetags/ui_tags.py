"""Template tags for UI helpers (datetime formatting, YouTube embed, etc.)."""

import re
from datetime import date as date_type

from django import template
from django.conf import settings
from django.template.defaultfilters import date as date_filter

register = template.Library()


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
def dict_key(d, key):
    """Look up a dictionary key (supports integer keys)."""
    return d.get(key)
