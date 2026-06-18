"""Template context processors."""

from __future__ import annotations

from django.conf import settings


def site_meta(request) -> dict[str, str]:
    """Expose site-wide metadata to all templates."""
    return {
        "SITE_NAME": "DokkiMashora LMS",
        "SITE_NAME_AR": "دكى مشورة",
        "DEFAULT_FROM_EMAIL": settings.DEFAULT_FROM_EMAIL,
    }
