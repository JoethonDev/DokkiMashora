"""Constants for the communications app."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

TEMPLATE_REGISTRATION_RECEIVED = "registration_received"
TEMPLATE_RECEIVED = TEMPLATE_REGISTRATION_RECEIVED
TEMPLATE_ENTRANCE_EXAM_INVITATION = "entrance_exam_invitation"
TEMPLATE_ACCEPTANCE = "acceptance"
TEMPLATE_REJECTION = "rejection"
TEMPLATE_ACTIVATION = "activation"

TEMPLATE_SLUGS = (
    TEMPLATE_REGISTRATION_RECEIVED,
    TEMPLATE_ENTRANCE_EXAM_INVITATION,
    TEMPLATE_ACCEPTANCE,
    TEMPLATE_REJECTION,
    TEMPLATE_ACTIVATION,
)

EMAIL_STATUS_QUEUED = "queued"
EMAIL_STATUS_SENT = "sent"
EMAIL_STATUS_FAILED = "failed"

EMAIL_STATUS_CHOICES = (
    (EMAIL_STATUS_QUEUED, _("Queued")),
    (EMAIL_STATUS_SENT, _("Sent")),
    (EMAIL_STATUS_FAILED, _("Failed")),
)

BULK_STATUS_DRAFT = "draft"
BULK_STATUS_SENT = "sent"
BULK_STATUS_PARTIAL = "partial"

BULK_STATUS_CHOICES = (
    (BULK_STATUS_DRAFT, _("Draft")),
    (BULK_STATUS_SENT, _("Sent")),
    (BULK_STATUS_PARTIAL, _("Partial")),
)
