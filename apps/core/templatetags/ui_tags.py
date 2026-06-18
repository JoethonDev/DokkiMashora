"""Template tags for UI helpers (datetime formatting, YouTube embed, etc.)."""

import re

from django import template
from django.conf import settings

register = template.Library()


@register.simple_tag
def dt(value):
    if not value:
        return ""
    return value.strftime(settings.SHORT_DATETIME_FORMAT)


_YOUTUBE_RE = re.compile(
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([\w-]+)"
)


@register.filter
def youtube_embed(url):
    """Convert a YouTube watch / short URL to an embed URL."""
    if not url:
        return ""
    match = _YOUTUBE_RE.search(url)
    if match:
        return f"https://www.youtube.com/embed/{match.group(1)}"
    return url
