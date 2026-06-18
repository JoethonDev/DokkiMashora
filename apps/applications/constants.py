"""Constants for the applications domain."""

from __future__ import annotations

APP_STATUS_PENDING = "pending"
APP_STATUS_UNDER_REVIEW = "under_review"
APP_STATUS_APPROVED = "approved"
APP_STATUS_REJECTED = "rejected"
APP_STATUS_ACTIVATED = "activated"

APP_STATUS_CHOICES = (
    (APP_STATUS_PENDING, "Pending"),
    (APP_STATUS_UNDER_REVIEW, "Under review"),
    (APP_STATUS_APPROVED, "Approved"),
    (APP_STATUS_REJECTED, "Rejected"),
    (APP_STATUS_ACTIVATED, "Activated"),
)

ACTIVE_APP_STATUSES = (
    APP_STATUS_PENDING,
    APP_STATUS_UNDER_REVIEW,
    APP_STATUS_APPROVED,
    APP_STATUS_ACTIVATED,
)
