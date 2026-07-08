from __future__ import annotations

from django.utils.translation import gettext_lazy as _

FIELD_TYPE_TEXT = "text"
FIELD_TYPE_TEXTAREA = "textarea"
FIELD_TYPE_NUMBER = "number"
FIELD_TYPE_DATE = "date"
FIELD_TYPE_CHOICE = "choice"
FIELD_TYPE_BOOLEAN = "boolean"

FIELD_TYPE_CHOICES = (
    (FIELD_TYPE_TEXT, _("Text")),
    (FIELD_TYPE_TEXTAREA, _("Textarea")),
    (FIELD_TYPE_NUMBER, _("Number")),
    (FIELD_TYPE_DATE, _("Date")),
    (FIELD_TYPE_CHOICE, _("Choice")),
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
