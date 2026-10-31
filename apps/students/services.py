"""Student profile services — profile update, apply eligibility."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.paginator import Paginator
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from apps.academics.constants import (
    ENROLLMENT_STATUS_ACTIVE,
    ENROLLMENT_STATUS_COMPLETED,
    ENROLLMENT_STATUS_PENDING,
)
from apps.academics.models import (
    Cohort,
    Course,
    CourseEnrollment,
    Diploma,
    DiplomaEnrollment,
)
from apps.accounts.services import lock_identity_writes
from apps.applications.constants import (
    DELIVERY_MODE_ONLINE,
    PROGRAM_APP_STATUS_REJECTED,
    PROGRAM_TYPE_COURSE,
    PROGRAM_TYPE_DIPLOMA,
)
from apps.applications.models import ProgramApplication
from apps.applications.services import (
    is_apply_target_open,
    open_diploma_ids_queryset,
    submit_program_application,
)
from apps.progression.constants import (
    DIPLOMA_COMPLETION_COMPLETED,
    DIPLOMA_COMPLETION_GRADUATED,
)
from apps.progression.models import StudentDiplomaCompletion
from apps.students.identity import validate_unique_identity_values

if TYPE_CHECKING:
    from apps.students.models import StudentProfile


def update_profile(profile, data: dict) -> StudentProfile:
    """Update student-allowed fields on the profile.

    Only keys present in the data dict are updated.  Restricted fields
    are ignored — callers must filter through the form or policy layer.
    """
    allowed = {"phone", "address"}
    with transaction.atomic():
        lock_identity_writes()
        updates = {key: value for key, value in data.items() if key in allowed}
        if "phone" in updates:
            updates["phone"], _identity = validate_unique_identity_values(
                country_code=profile.country_code,
                phone=updates["phone"],
                user_id=profile.user_id,
                profile_id=profile.pk,
            )
        for key, value in updates.items():
            if key in allowed:
                setattr(profile, key, value)
        profile.save(update_fields=updates.keys())
        user = profile.user
        if user.phone:
            user.phone = ""
            user.save(update_fields=["phone", "updated_at"])
    return profile


def check_apply_eligibility(
    profile: StudentProfile,
    program_type: str | None = None,
    cohort=None,
    course=None,
) -> dict:
    """Target-aware eligibility check.

    Returns a dict with keys:
      ``allowed`` (bool)
      ``reason_code`` (str | None) — one of completed, active_enrollment,
          pending_application, or None.
      ``reason`` (str | None) — human-readable message.

    When *program_type*/*cohort*/*course* are None, only checks for global
    blocking conditions (none currently). When a specific target is given,
    checks completions, active/pending enrollments, and blocking requests.
    """
    if program_type == PROGRAM_TYPE_DIPLOMA or cohort is not None:
        if cohort is None:
            return {"allowed": True, "reason_code": None, "reason": None}
        if not isinstance(cohort, Cohort):
            cohort = Cohort.objects.get(pk=cohort)
        diploma = cohort.diploma

        if not is_apply_target_open(cohort=cohort):
            return {
                "allowed": False,
                "reason_code": "target_closed",
                "reason": _("This program is no longer accepting applications."),
            }

        # Completed/graduated diploma
        if StudentDiplomaCompletion.objects.filter(
            student=profile,
            diploma_enrollment__cohort__diploma=diploma,
            status__in=(DIPLOMA_COMPLETION_COMPLETED, DIPLOMA_COMPLETION_GRADUATED),
        ).exists():
            return {
                "allowed": False,
                "reason_code": "completed",
                "reason": _("You have already completed this diploma."),
            }

        # Active/pending enrollment in any cohort of same diploma
        if DiplomaEnrollment.objects.filter(
            student=profile,
            cohort__diploma=diploma,
            status__in=(ENROLLMENT_STATUS_ACTIVE, ENROLLMENT_STATUS_PENDING),
        ).exists():
            return {
                "allowed": False,
                "reason_code": "active_enrollment",
                "reason": _("You are already enrolled in this diploma."),
            }

        # Blocking program application for any cohort of same diploma
        if ProgramApplication.objects.filter(
            student=profile,
            program_type=PROGRAM_TYPE_DIPLOMA,
            cohort__diploma=diploma,
        ).exclude(status=PROGRAM_APP_STATUS_REJECTED).exists():
            return {
                "allowed": False,
                "reason_code": "pending_application",
                "reason": _("You already have a pending application for this diploma."),
            }

        return {"allowed": True, "reason_code": None, "reason": None}

    if program_type == PROGRAM_TYPE_COURSE or course is not None:
        if course is None:
            return {"allowed": True, "reason_code": None, "reason": None}
        if not isinstance(course, Course):
            course = Course.objects.get(pk=course)
        if not is_apply_target_open(course=course):
            return {
                "allowed": False,
                "reason_code": "no_active_version",
                "reason": _("This course is not currently accepting applications."),
            }

        # Completed enrollment for any version of same course
        if CourseEnrollment.objects.filter(
            student=profile,
            course_version__course=course,
            status=ENROLLMENT_STATUS_COMPLETED,
        ).exists():
            return {
                "allowed": False,
                "reason_code": "completed",
                "reason": _("You have already completed this course."),
            }

        # Active/pending enrollment for any version of same course
        if CourseEnrollment.objects.filter(
            student=profile,
            course_version__course=course,
            status__in=(ENROLLMENT_STATUS_ACTIVE, ENROLLMENT_STATUS_PENDING),
        ).exists():
            return {
                "allowed": False,
                "reason_code": "active_enrollment",
                "reason": _("You are already enrolled in this course."),
            }

        # Blocking program application for same course
        if ProgramApplication.objects.filter(
            student=profile,
            program_type=PROGRAM_TYPE_COURSE,
            course=course,
        ).exclude(status=PROGRAM_APP_STATUS_REJECTED).exists():
            return {
                "allowed": False,
                "reason_code": "pending_application",
                "reason": _("You already have a pending application for this course."),
            }

        return {"allowed": True, "reason_code": None, "reason": None}

    return {"allowed": True, "reason_code": None, "reason": None}


def get_open_apply_targets(
    program_type: str,
    profile: StudentProfile | None = None,
    page: int = 1,
    page_size: int = 10,
) -> dict:
    """Return currently open targets for one program type.

    Diploma targets are diplomas that currently have at least one open cohort;
    the exact cohort is assigned automatically to the latest open cohort, so the
    applicant never selects a cohort. Course targets are courses with an active
    version. The open-target rule is the shared predicate
    ``is_apply_target_open``; per-profile exclusions (completed/enrolled/pending)
    are applied as set-based queryset filters, never per-target Python loops.
    """
    if program_type == PROGRAM_TYPE_DIPLOMA:
        diplomas_qs = (
            Diploma.objects.filter(pk__in=open_diploma_ids_queryset())
            .order_by("title")
        )
        if profile is not None:
            blocked_diploma_ids = _blocked_diploma_ids(profile)
            if blocked_diploma_ids:
                diplomas_qs = diplomas_qs.exclude(pk__in=blocked_diploma_ids)
        paginator = Paginator(diplomas_qs, page_size)
        page_obj = paginator.get_page(page)
        return {"items": list(page_obj.object_list), "has_more": page_obj.has_next(), "next_page": page_obj.next_page_number() if page_obj.has_next() else None}

    if program_type == PROGRAM_TYPE_COURSE:
        courses_qs = (
            Course.objects.filter(is_active=True, versions__is_active=True)
            .distinct()
            .order_by("title")
        )
        if profile is not None:
            blocked_course_ids = _blocked_course_ids(profile)
            if blocked_course_ids:
                courses_qs = courses_qs.exclude(id__in=blocked_course_ids)
        paginator = Paginator(courses_qs, page_size)
        page_obj = paginator.get_page(page)
        return {"items": list(page_obj.object_list), "has_more": page_obj.has_next(), "next_page": page_obj.next_page_number() if page_obj.has_next() else None}

    return {"items": [], "has_more": False, "next_page": None}


def _blocked_diploma_ids(profile: StudentProfile) -> set[int]:
    """Diploma ids the profile can no longer apply to (set-based equivalent of check_apply_eligibility)."""
    blocked = set(
        StudentDiplomaCompletion.objects.filter(
            student=profile,
            status__in=(DIPLOMA_COMPLETION_COMPLETED, DIPLOMA_COMPLETION_GRADUATED),
        ).values_list("diploma_enrollment__cohort__diploma_id", flat=True)
    )
    blocked |= set(
        DiplomaEnrollment.objects.filter(
            student=profile,
            status__in=(ENROLLMENT_STATUS_ACTIVE, ENROLLMENT_STATUS_PENDING),
        ).values_list("cohort__diploma_id", flat=True)
    )
    blocked |= set(
        ProgramApplication.objects.filter(
            student=profile,
            program_type=PROGRAM_TYPE_DIPLOMA,
        ).exclude(status=PROGRAM_APP_STATUS_REJECTED).values_list("cohort__diploma_id", flat=True)
    )
    return blocked


def _blocked_course_ids(profile: StudentProfile) -> set[int]:
    """Course ids the profile can no longer apply to (set-based equivalent of check_apply_eligibility)."""
    blocked = set(
        CourseEnrollment.objects.filter(
            student=profile,
            status__in=(ENROLLMENT_STATUS_ACTIVE, ENROLLMENT_STATUS_PENDING, ENROLLMENT_STATUS_COMPLETED),
        ).values_list("course_version__course_id", flat=True)
    )
    blocked |= set(
        ProgramApplication.objects.filter(
            student=profile,
            program_type=PROGRAM_TYPE_COURSE,
        ).exclude(status=PROGRAM_APP_STATUS_REJECTED).values_list("course_id", flat=True)
    )
    return blocked


def submit_apply_application(
    profile: StudentProfile,
    cohort=None,
    course=None,
    delivery_mode: str = DELIVERY_MODE_ONLINE,
) -> ProgramApplication:
    """Create a *new* ProgramApplication record for Apply."""
    if cohort is not None:
        return submit_program_application(
            profile,
            program_type=PROGRAM_TYPE_DIPLOMA,
            cohort=cohort,
            delivery_mode=delivery_mode,
        )
    if course is not None:
        return submit_program_application(
            profile, program_type=PROGRAM_TYPE_COURSE, course=course
        )

    raise ValueError(_("A cohort or course must be specified for application."))
