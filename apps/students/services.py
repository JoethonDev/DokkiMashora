"""Student profile services — profile update, reapply eligibility."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import transaction
from django.utils.translation import gettext_lazy as _

from apps.applications.constants import (
    APP_STATUS_PENDING,
    APP_STATUS_UNDER_REVIEW,
)
from apps.applications.models import Application

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


def check_reapply_eligibility(profile: StudentProfile, cohort=None) -> dict:
    """Return whether the student can submit a new application, and why.

    Returns a dict with keys ``allowed`` (bool) and ``reason`` (str | None).
    When *cohort* is provided, also checks that the student has no prior
    application (across *all* statuses) for that same cohort.
    """
    pending_or_review = Application.objects.filter(
        student=profile,
        status__in=(APP_STATUS_PENDING, APP_STATUS_UNDER_REVIEW),
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
        prior = Application.objects.filter(
            student=profile,
            cohort=cohort,
        ).exists()
        if prior:
            return {
                "allowed": False,
                "reason": _("You already have an application for this cohort. "
                             "Choose a different program or cohort."),
            }

    return {"allowed": True, "reason": None}


def submit_reapply_application(profile: StudentProfile, cohort=None) -> Application:
    """Create a *new* Application record for a reapply.

    Always creates a fresh row.  Callers must have already verified eligibility.
    Raises ValueError if the profile has an open application that is still
    pending or under review (belt-and-suspenders guard), or if *cohort*
    has already been applied for.
    """
    from apps.academics.models import Cohort

    pending_or_review = Application.objects.filter(
        student=profile,
        status__in=(APP_STATUS_PENDING, APP_STATUS_UNDER_REVIEW),
    ).exists()

    if pending_or_review:
        raise ValueError(
            _("You already have an application that is under review. "
              "Please wait for a decision before submitting a new one.")
        )

    if cohort is not None and not isinstance(cohort, Cohort):
        cohort = Cohort.objects.get(pk=cohort)

    if cohort is not None:
        prior = Application.objects.filter(student=profile, cohort=cohort).exists()
        if prior:
            raise ValueError(
                _("You already have an application for this cohort. "
                  "Choose a different program or cohort.")
            )

    return Application.objects.create(student=profile, status=APP_STATUS_PENDING, cohort=cohort)
