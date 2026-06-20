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
