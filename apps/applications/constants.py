"""Constants for the applications domain."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

APP_STATUS_PENDING = "pending"
APP_STATUS_UNDER_REVIEW = "under_review"
APP_STATUS_APPROVED = "approved"
APP_STATUS_REJECTED = "rejected"
APP_STATUS_ACTIVATED = "activated"

APP_STATUS_CHOICES = (
    (APP_STATUS_PENDING, _("Pending")),
    (APP_STATUS_UNDER_REVIEW, _("Under review")),
    (APP_STATUS_APPROVED, _("Approved")),
    (APP_STATUS_REJECTED, _("Rejected")),
    (APP_STATUS_ACTIVATED, _("Activated")),
)

ACTIVE_APP_STATUSES = (
    APP_STATUS_PENDING,
    APP_STATUS_UNDER_REVIEW,
    APP_STATUS_APPROVED,
    APP_STATUS_ACTIVATED,
)

PROGRAM_APP_STATUS_SUBMITTED = "submitted"
PROGRAM_APP_STATUS_UNDER_REVIEW = "under_review"
PROGRAM_APP_STATUS_INVITED = "invited_to_entrance"
PROGRAM_APP_STATUS_ACCEPTED = "accepted"
PROGRAM_APP_STATUS_REJECTED = "rejected"
PROGRAM_APP_STATUS_ACTIVATED = "activated"

PROGRAM_APP_STATUS_CHOICES = (
    (PROGRAM_APP_STATUS_SUBMITTED, _("Submitted")),
    (PROGRAM_APP_STATUS_UNDER_REVIEW, _("Under review")),
    (PROGRAM_APP_STATUS_INVITED, _("Invited to Entrance Assessment")),
    (PROGRAM_APP_STATUS_ACCEPTED, _("Accepted")),
    (PROGRAM_APP_STATUS_REJECTED, _("Rejected")),
    (PROGRAM_APP_STATUS_ACTIVATED, _("Activated")),
)

ACTIVE_PROGRAM_APP_STATUSES = (
    PROGRAM_APP_STATUS_SUBMITTED,
    PROGRAM_APP_STATUS_UNDER_REVIEW,
    PROGRAM_APP_STATUS_INVITED,
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_ACTIVATED,
)

PROGRAM_TYPE_DIPLOMA = "diploma"
PROGRAM_TYPE_COURSE = "course"

PROGRAM_TYPE_CHOICES = (
    (PROGRAM_TYPE_DIPLOMA, _("Diploma (Cohort)")),
    (PROGRAM_TYPE_COURSE, _("Course")),
)
