"""Template context processors."""

from __future__ import annotations

from django.conf import settings

from apps.core.permissions import is_admin, is_student


def site_meta(request) -> dict[str, object]:
    """Expose site-wide metadata to all templates."""
    user = request.user
    is_authenticated = user.is_authenticated
    can_view_staff_nav = bool(is_authenticated and (user.is_superuser or user.is_staff_member))
    can_view_student_nav = bool(is_authenticated and is_student(user) and not can_view_staff_nav)

    return {
        "SITE_NAME": settings.SITE_NAME,
        "SITE_NAME_AR": settings.SITE_NAME_AR,
        "DEFAULT_FROM_EMAIL": settings.DEFAULT_FROM_EMAIL,
        "CAN_VIEW_DESIGN_SHOWCASE": is_admin(user),
        "CAN_VIEW_STAFF_NAV": can_view_staff_nav,
        "CAN_VIEW_STUDENT_NAV": can_view_student_nav,
    }
