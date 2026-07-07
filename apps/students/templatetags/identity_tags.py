"""Template tags for country-aware identity label rendering."""
from __future__ import annotations

from django import template

from apps.students.identity import identity_label

register = template.Library()


@register.simple_tag
def identity_label_for(country_code: str) -> str:
    return identity_label(country_code)
