from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from apps.applications.constants import (
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_DOCUMENTS_REQUESTED,
    PROGRAM_APP_STATUS_DOCUMENTS_SUBMITTED,
    PROGRAM_APP_STATUS_PAYMENT_PENDING,
    PROGRAM_APP_STATUS_READY_FOR_ACTIVATION,
    PROGRAM_APP_STATUS_SURVEY_REQUESTED,
    PROGRAM_APP_STATUS_SURVEY_SUBMITTED,
)

FIELD_TYPE_TEXT = "text"
FIELD_TYPE_TEXTAREA = "textarea"
FIELD_TYPE_NUMBER = "number"
FIELD_TYPE_DATE = "date"
FIELD_TYPE_CHOICE = "choice"
FIELD_TYPE_MULTI_CHOICE = "multi_choice"
FIELD_TYPE_BOOLEAN = "boolean"

FIELD_TYPE_CHOICES = (
    (FIELD_TYPE_TEXT, _("Text")),
    (FIELD_TYPE_TEXTAREA, _("Textarea")),
    (FIELD_TYPE_NUMBER, _("Number")),
    (FIELD_TYPE_DATE, _("Date")),
    (FIELD_TYPE_CHOICE, _("Choice")),
    (FIELD_TYPE_MULTI_CHOICE, _("Multiple choice")),
    (FIELD_TYPE_BOOLEAN, _("Yes/No")),
)

INTAKE_REQUEST_STATUS_REQUESTED = "requested"
INTAKE_REQUEST_STATUS_SUBMITTED = "submitted"
INTAKE_REQUEST_STATUS_EXPIRED = "expired"
INTAKE_REQUEST_STATUS_CANCELLED = "cancelled"

INTAKE_REQUEST_STATUS_CHOICES = (
    (INTAKE_REQUEST_STATUS_REQUESTED, _("Requested")),
    (INTAKE_REQUEST_STATUS_SUBMITTED, _("Submitted")),
    (INTAKE_REQUEST_STATUS_EXPIRED, _("Expired")),
    (INTAKE_REQUEST_STATUS_CANCELLED, _("Cancelled")),
)

# Application statuses from which an intake (survey) request may still be sent.
# Intake is a post-decision gate. Pre-decision statuses intentionally stay out
# of this set so a stale link or an over-eager admin action cannot bypass the
# entrance/interview decision and, for Diploma, the document stage.
INTAKE_REQUESTABLE_STATUSES = (
    PROGRAM_APP_STATUS_PAYMENT_PENDING,
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_DOCUMENTS_REQUESTED,
    PROGRAM_APP_STATUS_DOCUMENTS_SUBMITTED,
    PROGRAM_APP_STATUS_SURVEY_REQUESTED,
    PROGRAM_APP_STATUS_SURVEY_SUBMITTED,
    PROGRAM_APP_STATUS_READY_FOR_ACTIVATION,
)
