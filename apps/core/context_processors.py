"""Template context processors."""

from __future__ import annotations

import logging

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
from apps.core.navigation import workspace_navigation
from django.contrib.auth.models import AnonymousUser

from apps.core.permissions import is_admin, is_student
from django.urls import reverse

logger = logging.getLogger(__name__)


def site_meta(request) -> dict[str, object]:
    """Expose site-wide metadata to all templates."""
    user = getattr(request, "user", AnonymousUser())
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

    # Back-URL mapping: current view_name → parent view_name
    # Used by _page_header.html to generate a back button when back_url is not given
    BACK_URL_MAP = {
        "entrance_exams:admin_detail": "entrance_exams:admin_list",
        "entrance_exams:admin_edit_exam": "entrance_exams:admin_detail",
        "entrance_exams:admin_preview": "entrance_exams:admin_detail",
        "entrance_exams:admin_add_question": "entrance_exams:admin_detail",
        "entrance_exams:admin_edit_question": "entrance_exams:admin_detail",
        "entrance_exams:admin_cohort_email": "entrance_exams:admin_detail",
        "entrance_exams:admin_create_exam": "entrance_exams:admin_list",
        "entrance_exams:admin_email_log_list": "entrance_exams:admin_list",
        "entrance_exams:admin_grade_attempt": "entrance_exams:admin_grade_list",
        "entrance_exams:admin_grade_list": "entrance_exams:admin_list",
        "assessments:admin_detail": "assessments:admin_list",
        "assessments:admin_edit": "assessments:admin_detail",
        "assessments:admin_preview": "assessments:admin_detail",
        "academics:admin_diploma_detail": "academics:admin_diploma_list",
        "academics:admin_diploma_edit": "academics:admin_diploma_detail",
        "academics:admin_course_detail": "academics:admin_course_list",
        "academics:admin_course_edit": "academics:admin_course_detail",
        "academics:admin_student_detail": "academics:admin_student_list",
        "academics:admin_student_edit": "academics:admin_student_detail",
        "intake_forms:admin_intake_form_detail": "intake_forms:admin_intake_form_list",
        "communications:bulk_email_detail": "communications:bulk_email_history",
        "communications:admin_template_edit": "communications:admin_template_list",
        "assessments:admin_section_create": "assessments:admin_detail",
        "assessments:admin_section_edit": "assessments:admin_detail",
    }

    page_back_url = ""
    view_name = getattr(request.resolver_match, "view_name", "")
    if view_name in BACK_URL_MAP:
        parent_name = BACK_URL_MAP[view_name]
        kwargs = dict(request.resolver_match.kwargs) if request.resolver_match else {}
        # Strip child-specific kwargs (things with _id suffix beyond the parent's primary key)
        parent_id_keys = {"assessment_id", "exam_id", "diploma_id", "cohort_id", "semester_id",
                          "lecture_id", "question_id", "course_id", "course_version_id",
                          "user_id", "staff_id", "student_id"}
        known = {k for k in kwargs if k in parent_id_keys}
        if known:
            kwargs = {k: kwargs[k] for k in known}
        try:
            page_back_url = reverse(parent_name, kwargs=kwargs)
        except Exception:
            logger.warning("PAGE_BACK_URL reverse failed for %s -> %s", view_name, parent_name)

    landing_settings = LandingPageSettings.load()
    return {
        "SITE_NAME": settings.SITE_NAME,
        "SITE_NAME_AR": settings.SITE_NAME_AR,
        "DEFAULT_FROM_EMAIL": settings.DEFAULT_FROM_EMAIL,
        "WORKSPACE_NAVIGATION": workspace_navigation(user),
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
        "PAGE_BACK_URL": page_back_url,
    }
