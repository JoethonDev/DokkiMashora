"""Constants for the applications domain."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

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
