"""Template tags for UI helpers (datetime formatting, etc.)."""

from django import template
from django.conf import settings

register = template.Library()


@register.simple_tag
def dt(value):
    """Render a datetime value in the project's standard DATETIME_FORMAT."""
    if not value:
        return ""
    return value.strftime(settings.SHORT_DATETIME_FORMAT)
