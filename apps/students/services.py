"""Student profile services — profile update, apply eligibility."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import transaction
from django.utils.translation import gettext_lazy as _

from apps.academics.constants import (
    ENROLLMENT_STATUS_ACTIVE,
    ENROLLMENT_STATUS_COMPLETED,
    ENROLLMENT_STATUS_PENDING,
)
from apps.academics.models import Cohort, Course as CourseModel
from apps.applications.constants import (
    PROGRAM_APP_STATUS_REJECTED,
    PROGRAM_TYPE_COURSE,
    PROGRAM_TYPE_DIPLOMA,
)
from apps.applications.models import ProgramApplication
from apps.applications.services import submit_program_application
from apps.progression.constants import (
    DIPLOMA_COMPLETION_COMPLETED,
    DIPLOMA_COMPLETION_GRADUATED,
)

if TYPE_CHECKING:
    from apps.students.models import StudentProfile


def update_profile(profile, data: dict) -> StudentProfile:
    """Update student-allowed fields on the profile.

    Only keys present in the data dict are updated.  Restricted fields
    are ignored — callers must filter through the form or policy layer.
    """
    allowed = {"phone", "address"}
    with transaction.atomic():
        for key, value in data.items():
            if key in allowed:
                setattr(profile, key, value)
        profile.save(update_fields=allowed & set(data.keys()))
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

        # Completed/graduated diploma
        from apps.progression.models import StudentDiplomaCompletion
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
        from apps.academics.models import DiplomaEnrollment
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
        if not isinstance(course, CourseModel):
            course = CourseModel.objects.get(pk=course)

        # Completed enrollment for any version of same course
        from apps.academics.models import CourseEnrollment
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


def submit_apply_application(profile: StudentProfile, cohort=None, course=None) -> ProgramApplication:
    """Create a *new* ProgramApplication record for Apply."""
    if cohort is not None:
        return submit_program_application(
            profile, program_type=PROGRAM_TYPE_DIPLOMA, cohort=cohort
        )
    if course is not None:
        return submit_program_application(
            profile, program_type=PROGRAM_TYPE_COURSE, course=course
        )

    raise ValueError(_("A cohort or course must be specified for application."))

