from __future__ import annotations

from django.utils.translation import gettext_lazy as _

UPLOAD_STATUS_PENDING = "pending"
UPLOAD_STATUS_PREVIEWED = "previewed"
UPLOAD_STATUS_CONFIRMED = "confirmed"
UPLOAD_STATUS_FAILED = "failed"

UPLOAD_STATUS_CHOICES = (
    (UPLOAD_STATUS_PENDING, _("Pending")),
    (UPLOAD_STATUS_PREVIEWED, _("Previewed")),
    (UPLOAD_STATUS_CONFIRMED, _("Confirmed")),
    (UPLOAD_STATUS_FAILED, _("Failed")),
)

ROW_STATUS_PARSED = "parsed"
ROW_STATUS_MATCHED = "matched"
ROW_STATUS_UNMATCHED = "unmatched"
ROW_STATUS_AMBIGUOUS = "ambiguous"
ROW_STATUS_CREATED = "created"
ROW_STATUS_UPDATED = "updated"
ROW_STATUS_SKIPPED = "skipped"
ROW_STATUS_FAILED = "failed"

ROW_STATUS_CHOICES = (
    (ROW_STATUS_PARSED, _("Parsed")),
    (ROW_STATUS_MATCHED, _("Matched")),
    (ROW_STATUS_UNMATCHED, _("Unmatched")),
    (ROW_STATUS_AMBIGUOUS, _("Ambiguous")),
    (ROW_STATUS_CREATED, _("Created")),
    (ROW_STATUS_UPDATED, _("Updated")),
    (ROW_STATUS_SKIPPED, _("Skipped")),
    (ROW_STATUS_FAILED, _("Failed")),
)

MATCH_METHOD_NATIONAL_ID = "national_id"
MATCH_METHOD_EMAIL = "email"
MATCH_METHOD_PHONE = "phone"
MATCH_METHOD_NONE = "none"
MATCH_METHOD_AMBIGUOUS = "ambiguous"

MATCH_METHOD_CHOICES = (
    (MATCH_METHOD_NATIONAL_ID, _("National ID")),
    (MATCH_METHOD_EMAIL, _("Email")),
    (MATCH_METHOD_PHONE, _("Phone")),
    (MATCH_METHOD_NONE, _("None")),
    (MATCH_METHOD_AMBIGUOUS, _("Ambiguous")),
)

PAYMENT_STATUS_PAID = "paid"
PAYMENT_STATUS_UNPAID = "unpaid"
PAYMENT_STATUS_PARTIAL = "partial"
PAYMENT_STATUS_REFUNDED = "refunded"
PAYMENT_STATUS_FAILED = "failed"

PAYMENT_STATUS_CHOICES = (
    (PAYMENT_STATUS_UNPAID, _("Unpaid")),
    (PAYMENT_STATUS_PAID, _("Paid")),
    (PAYMENT_STATUS_PARTIAL, _("Partial")),
    (PAYMENT_STATUS_REFUNDED, _("Refunded")),
    (PAYMENT_STATUS_FAILED, _("Failed")),
)

CURRENCY_EGP = "EGP"
CURRENCY_CHOICES = (
    (CURRENCY_EGP, _("EGP")),
)

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

REQUIRED_CSV_HEADERS = ["national_id", "amount", "payment_date"]
OPTIONAL_CSV_HEADERS = [
    "external_reference", "email", "phone", "notes",
    "application_id", "diploma_enrollment_id", "course_enrollment_id",
    "target_type", "target_ref",
]

ALL_CSV_HEADERS = REQUIRED_CSV_HEADERS + list(OPTIONAL_CSV_HEADERS)
