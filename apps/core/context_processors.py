"""Template context processors."""

from __future__ import annotations

from django.conf import settings

from apps.core.permissions import is_admin


def site_meta(request) -> dict[str, object]:
    """Expose site-wide metadata to all templates."""
    return {
        "SITE_NAME": "DokkiMashora LMS",
        "SITE_NAME_AR": "دكى مشورة",
        "DEFAULT_FROM_EMAIL": settings.DEFAULT_FROM_EMAIL,
        "CAN_VIEW_DESIGN_SHOWCASE": is_admin(request.user),
    }
