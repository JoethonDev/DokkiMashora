"""Constants for the communications app."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

TEMPLATE_REGISTRATION_RECEIVED = "registration_received"
TEMPLATE_RECEIVED = TEMPLATE_REGISTRATION_RECEIVED
TEMPLATE_ENTRANCE_EXAM_INVITATION = "entrance_exam_invitation"
TEMPLATE_ENTRANCE_EXAM_PASS = "entrance_exam_pass"
TEMPLATE_ENTRANCE_EXAM_FAIL = "entrance_exam_fail"
TEMPLATE_ACCEPTANCE = "acceptance"
TEMPLATE_REJECTION = "rejection"
TEMPLATE_ACTIVATION = "activation"

TEMPLATE_SLUGS = (
    TEMPLATE_REGISTRATION_RECEIVED,
    TEMPLATE_ENTRANCE_EXAM_INVITATION,
    TEMPLATE_ENTRANCE_EXAM_PASS,
    TEMPLATE_ENTRANCE_EXAM_FAIL,
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

REQUIRED_VARIABLES_BY_SLUG: dict[str, list[str]] = {
    TEMPLATE_REGISTRATION_RECEIVED: ["full_name"],
    TEMPLATE_ENTRANCE_EXAM_INVITATION: ["full_name", "exam_title", "cohort_name"],
    TEMPLATE_ENTRANCE_EXAM_PASS: ["full_name", "exam_title"],
    TEMPLATE_ENTRANCE_EXAM_FAIL: ["full_name", "exam_title"],
    TEMPLATE_ACCEPTANCE: ["full_name", "cohort_name"],
    TEMPLATE_REJECTION: ["full_name", "cohort_name"],
    TEMPLATE_ACTIVATION: ["full_name", "cohort_name"],
}

SAMPLE_CONTEXT_BY_SLUG: dict[str, dict[str, str]] = {
    TEMPLATE_REGISTRATION_RECEIVED: {"full_name": "ماري جرجس"},
    TEMPLATE_ENTRANCE_EXAM_INVITATION: {"full_name": "ماري جرجس", "exam_title": "امتحان القبول للدبلومة", "cohort_name": "دفعة ٢٠٢٦"},
    TEMPLATE_ENTRANCE_EXAM_PASS: {"full_name": "ماري جرجس", "exam_title": "امتحان القبول للدبلومة"},
    TEMPLATE_ENTRANCE_EXAM_FAIL: {"full_name": "ماري جرجس", "exam_title": "امتحان القبول للدبلومة"},
    TEMPLATE_ACCEPTANCE: {"full_name": "ماري جرجس", "cohort_name": "دفعة ٢٠٢٦"},
    TEMPLATE_REJECTION: {"full_name": "ماري جرجس", "cohort_name": "دفعة ٢٠٢٦"},
    TEMPLATE_ACTIVATION: {"full_name": "ماري جرجس", "cohort_name": "دفعة ٢٠٢٦"},
}

TEMPLATE_SAMPLE_CONTEXT = SAMPLE_CONTEXT_BY_SLUG
