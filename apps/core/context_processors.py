"""Template context processors."""

from __future__ import annotations

from django.conf import settings

from apps.accounts.constants import (
    GROUP_ADMIN,
    GROUP_MODERATOR,
    GROUP_REVIEWER,
    GROUP_SECRETARY,
    GROUP_SUPER_ADMIN,
    GROUP_TEACHER,
)
from apps.core.models import LandingPageSettings
from apps.core.permissions import is_admin, is_student


def site_meta(request) -> dict[str, object]:
    """Expose site-wide metadata to all templates."""
    user = request.user
    is_authenticated = user.is_authenticated
    user_roles = set(user.groups.values_list("name", flat=True)) if is_authenticated else set()
    can_view_staff_nav = bool(is_authenticated and (user.is_superuser or (user_roles & {
        GROUP_SUPER_ADMIN, GROUP_ADMIN, GROUP_MODERATOR, GROUP_TEACHER})))
    can_view_student_nav = bool(is_authenticated and is_student(user) and not can_view_staff_nav)

    # Per-role capability flags for nav scoping
    can_view_admin_nav = bool(is_authenticated and (user.is_superuser or user_roles & {GROUP_ADMIN, GROUP_SUPER_ADMIN}))
    can_view_moderator_nav = bool(is_authenticated and GROUP_MODERATOR in user_roles)
    can_view_teacher_nav = bool(is_authenticated and GROUP_TEACHER in user_roles)
    can_view_reviewer_nav = bool(is_authenticated and GROUP_REVIEWER in user_roles)
    can_view_secretary_nav = bool(is_authenticated and GROUP_SECRETARY in user_roles)
    can_view_manage_nav = can_view_admin_nav or can_view_moderator_nav

    landing_settings = LandingPageSettings.load()
    return {
        "SITE_NAME": settings.SITE_NAME,
        "SITE_NAME_AR": settings.SITE_NAME_AR,
        "DEFAULT_FROM_EMAIL": settings.DEFAULT_FROM_EMAIL,
        "CAN_VIEW_DESIGN_SHOWCASE": is_admin(user),
        "CAN_VIEW_STAFF_NAV": can_view_staff_nav,
        "CAN_VIEW_STUDENT_NAV": can_view_student_nav,
        "CAN_VIEW_ADMIN_NAV": can_view_admin_nav,
        "CAN_VIEW_MODERATOR_NAV": can_view_moderator_nav,
        "CAN_VIEW_TEACHER_NAV": can_view_teacher_nav,
        "CAN_VIEW_REVIEWER_NAV": can_view_reviewer_nav,
        "CAN_VIEW_SECRETARY_NAV": can_view_secretary_nav,
        "CAN_VIEW_MANAGE_NAV": can_view_manage_nav,
        "LANDING_SETTINGS": landing_settings,
        "FOOTER_CONTENT": landing_settings.localized_content(
            getattr(request, "LANGUAGE_CODE", settings.LANGUAGE_CODE)
        ),
    }
