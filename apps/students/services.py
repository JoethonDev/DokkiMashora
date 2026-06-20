"""Student profile services — profile update, reapply eligibility."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import transaction
from django.utils.translation import gettext_lazy as _

from apps.applications.constants import (
    PROGRAM_APP_STATUS_REJECTED,
    PROGRAM_APP_STATUS_SUBMITTED,
    PROGRAM_APP_STATUS_UNDER_REVIEW,
)
from apps.applications.models import ProgramApplication

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


def check_reapply_eligibility(profile: StudentProfile, cohort=None, course=None) -> dict:
    """Return whether the student can submit a new program application, and why.

    Returns a dict with keys ``allowed`` (bool) and ``reason`` (str | None).
    When *cohort* or *course* is provided, also checks for existing requests.
    """
    pending_or_review = ProgramApplication.objects.filter(
        student=profile,
        status__in=(PROGRAM_APP_STATUS_SUBMITTED, PROGRAM_APP_STATUS_UNDER_REVIEW),
    ).exists()

    if pending_or_review:
        return {
            "allowed": False,
            "reason": _("You already have an application that is under review. "
                         "Please wait for a decision before submitting a new one."),
        }

    if cohort is not None:
        from apps.academics.models import Cohort
        if not isinstance(cohort, Cohort):
            cohort = Cohort.objects.get(pk=cohort)
        prior = ProgramApplication.objects.filter(
            student=profile,
            cohort=cohort,
        ).exclude(status=PROGRAM_APP_STATUS_REJECTED).exists()
        if prior:
            return {
                "allowed": False,
                "reason": _("You already have an application for this cohort. "
                             "Choose a different program or cohort."),
            }

    if course is not None:
        from apps.academics.models import Course as CourseModel
        if not isinstance(course, CourseModel):
            course = CourseModel.objects.get(pk=course)
        prior = ProgramApplication.objects.filter(
            student=profile,
            course=course,
        ).exclude(status=PROGRAM_APP_STATUS_REJECTED).exists()
        if prior:
            return {
                "allowed": False,
                "reason": _("You already have an application for this course. "
                             "Choose a different course."),
            }

    return {"allowed": True, "reason": None}


def submit_reapply_application(profile: StudentProfile, cohort=None, course=None) -> ProgramApplication:
    """Create a *new* ProgramApplication record for a reapply.

    Always creates a fresh row.  Callers must have already verified eligibility.
    Raises ValueError if the student has an open application that is still
    pending or under review (belt-and-suspenders guard), or if the target
    has already been applied for.
    """
    from apps.applications.constants import PROGRAM_TYPE_COURSE, PROGRAM_TYPE_DIPLOMA
    from apps.applications.services import submit_program_application

    if cohort is not None:
        return submit_program_application(
            profile, program_type=PROGRAM_TYPE_DIPLOMA, cohort=cohort
        )
    if course is not None:
        return submit_program_application(
            profile, program_type=PROGRAM_TYPE_COURSE, course=course
        )

    raise ValueError(_("A cohort or course must be specified for reapplication."))
