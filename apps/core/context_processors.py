"""Template context processors."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from django.conf import settings
from django.utils.http import url_has_allowed_host_and_scheme

from apps.accounts.constants import (
    GROUP_ADMIN,
    GROUP_MODERATOR,
    GROUP_REVIEWER,
    GROUP_SECRETARY,
    GROUP_SUPER_ADMIN,
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
        GROUP_SUPER_ADMIN, GROUP_ADMIN, GROUP_MODERATOR})))
    can_view_student_nav = bool(is_authenticated and is_student(user) and not can_view_staff_nav)

    # Per-role capability flags for nav scoping
    can_view_admin_nav = bool(is_authenticated and (user.is_superuser or user_roles & {GROUP_ADMIN, GROUP_SUPER_ADMIN}))
    can_view_moderator_nav = bool(is_authenticated and GROUP_MODERATOR in user_roles)
    can_view_reviewer_nav = bool(is_authenticated and GROUP_REVIEWER in user_roles)
    can_view_secretary_nav = bool(is_authenticated and GROUP_SECRETARY in user_roles)
    can_view_manage_nav = can_view_admin_nav or can_view_moderator_nav

    # Back-URL mapping: current view_name → (parent view_name, kwarg passthrough).
    # Only kwargs the parent route actually accepts are forwarded; child-only
    # ids are never passed. This is the single canonical parent owner — pages
    # must not fall back to browser history.
    BACK_URL_MAP = {
        "entrance_exams:admin_detail": ("entrance_exams:admin_list", {}),
        "entrance_exams:admin_edit_exam": ("entrance_exams:admin_detail", {"exam_id": "exam_id"}),
        "entrance_exams:admin_preview": ("entrance_exams:admin_detail", {"exam_id": "exam_id"}),
        "entrance_exams:admin_add_question": ("entrance_exams:admin_detail", {"exam_id": "exam_id"}),
        "entrance_exams:admin_edit_question": ("entrance_exams:admin_detail", {"exam_id": "exam_id"}),
        "entrance_exams:admin_cohort_email": ("entrance_exams:admin_detail", {"exam_id": "exam_id"}),
        "entrance_exams:admin_create_exam": ("entrance_exams:admin_list", {}),
        "entrance_exams:admin_email_log_list": ("entrance_exams:admin_list", {}),
        "entrance_exams:admin_grade_attempt": ("entrance_exams:admin_grade_list", {}),
        "entrance_exams:admin_grade_list": ("entrance_exams:admin_list", {}),
        "assessments:admin_detail": ("assessments:admin_list", {}),
        "assessments:admin_edit": ("assessments:admin_detail", {"assessment_id": "assessment_id"}),
        "assessments:admin_preview": ("assessments:admin_detail", {"assessment_id": "assessment_id"}),
        "assessments:admin_grade_detail": ("assessments:admin_grade_list", {}),
        "assessments:admin_grade_answer": ("assessments:admin_grade_list", {}),
        "assessments:admin_grade_homework": ("assessments:admin_grade_list", {}),
        "assessments:admin_exam_review_detail": ("assessments:admin_exam_review_list", {}),
        "assessments:admin_roster": ("assessments:admin_detail", {"assessment_id": "assessment_id"}),
        "assessments:admin_exceptional_access": ("assessments:admin_detail", {"assessment_id": "assessment_id"}),
        "assessments:admin_offline_grade_entry": ("assessments:admin_detail", {"assessment_id": "assessment_id"}),
        "assessments:reviewer_section_grading": ("assessments:reviewer_dashboard", {}),
        "assessments:reviewer_online_grading": ("assessments:reviewer_dashboard", {}),
        "assessments:admin_reviewer_assignment_create": ("assessments:admin_reviewer_assignment_list", {}),
        "academics:admin_diploma_detail": ("academics:admin_diploma_list", {}),
        "academics:admin_diploma_edit": ("academics:admin_diploma_detail", {"diploma_id": "diploma_id"}),
        "academics:admin_course_detail": ("academics:admin_course_list", {}),
        "academics:admin_course_edit": ("academics:admin_course_detail", {"course_id": "course_id"}),
        "academics:admin_lecture_preview": (
            "academics:admin_version_detail",
            {"course_id": "course_id", "version_id": "version_id"},
        ),
        "academics:admin_version_detail": (
            "academics:admin_course_detail", {"course_id": "course_id"},
        ),
        "academics:admin_diploma_lecture_preview": (
            "academics:admin_diploma_detail", {"diploma_id": "diploma_id"},
        ),
        "academics:admin_student_detail": ("academics:admin_student_list", {}),
        "academics:admin_student_edit": ("academics:admin_student_detail", {"student_id": "student_id"}),
        "intake_forms:admin_intake_form_detail": ("intake_forms:admin_intake_form_list", {}),
        "communications:bulk_email_detail": ("communications:bulk_email_history", {}),
        "communications:bulk_email_preview": ("communications:bulk_email_history", {}),
        "communications:admin_footer_edit": ("communications:bulk_email_history", {}),
        "communications:admin_template_edit": ("communications:admin_template_list", {}),
        "assessments:admin_section_create": ("assessments:admin_detail", {"assessment_id": "assessment_id"}),
        "assessments:admin_section_edit": ("assessments:admin_detail", {"assessment_id": "assessment_id"}),
        "assessments:admin_lecture_bank_list": (
            "academics:admin_diploma_lecture_list",
            {"diploma_id": "diploma_id", "cohort_id": "cohort_id", "semester_id": "semester_id"},
        ),
        "applications:program_detail": ("applications:program_list", {}),
        "accounts:profile": ("accounts:dashboard", {}),
        "attendance:attendance_session_create": ("attendance:attendance_session_list", {}),
        "attendance:attendance_session_detail": ("attendance:attendance_session_list", {}),
        "attendance:attendance_scan": ("attendance:attendance_session_list", {}),
        "certificates:admin_template_editor": (
            "certificates:admin_template_edit", {"template_id": "template_id"},
        ),
        "progression:admin_rejoin_eligibility": ("progression:admin_dashboard", {}),
        "progression:admin_create_rejoin_plan": ("progression:admin_rejoin_eligibility", {}),
    }

    # Prefer an explicit return path carried by the link/form. For older links
    # that do not carry ``next`` yet, use a same-host referrer when it points
    # to a different page. A refresh of the current page is ignored so the
    # canonical parent remains the stable fallback.
    page_return_url = ""
    requested_return_url = request.POST.get("next", "") or request.GET.get("next", "")
    if requested_return_url and url_has_allowed_host_and_scheme(
        requested_return_url,
        allowed_hosts={request.get_host()},
    ):
        page_return_url = requested_return_url
    else:
        referrer = request.META.get("HTTP_REFERER", "")
        if (
            referrer
            and url_has_allowed_host_and_scheme(referrer, allowed_hosts={request.get_host()})
            and urlsplit(referrer).path.rstrip("/") != request.path.rstrip("/")
        ):
            page_return_url = referrer

    page_back_url = ""
    view_name = getattr(request.resolver_match, "view_name", "")
    if view_name in BACK_URL_MAP:
        parent_name, kwarg_map = BACK_URL_MAP[view_name]
        kwargs = {
            parent_key: request.resolver_match.kwargs[child_key]
            for child_key, parent_key in kwarg_map.items()
            if child_key in request.resolver_match.kwargs
        }
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
        "CAN_VIEW_REVIEWER_NAV": can_view_reviewer_nav,
        "CAN_VIEW_SECRETARY_NAV": can_view_secretary_nav,
        "CAN_VIEW_MANAGE_NAV": can_view_manage_nav,
        "LANDING_SETTINGS": landing_settings,
        "FOOTER_CONTENT": landing_settings.localized_content(
            getattr(request, "LANGUAGE_CODE", settings.LANGUAGE_CODE)
        ),
        "PAGE_RETURN_URL": page_return_url,
        "PAGE_BACK_URL": page_back_url,
    }
