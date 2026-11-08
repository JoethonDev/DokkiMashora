"""Constants for the communications app."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

#: Transactional email is Arabic-only by product decision; the English branch
#: stored in legacy templates is never selected at send or preview time.
TRANSACTIONAL_EMAIL_LANGUAGE = "ar"

TEMPLATE_REGISTRATION_RECEIVED = "registration_received"
TEMPLATE_RECEIVED = TEMPLATE_REGISTRATION_RECEIVED
TEMPLATE_ENTRANCE_EXAM_INVITATION = "entrance_exam_invitation"
TEMPLATE_ENTRANCE_EXAM_PASS = "entrance_exam_pass"
TEMPLATE_ENTRANCE_EXAM_FAIL = "entrance_exam_fail"
TEMPLATE_EXAM_INVITATION = "exam_invitation"
TEMPLATE_ACCEPTANCE = "acceptance"
TEMPLATE_REJECTION = "rejection"
TEMPLATE_ACTIVATION = "activation"
TEMPLATE_PAYMENT_NOTIFICATION = "payment_notification"
TEMPLATE_COURSE_PAYMENT_INSTRUCTIONS = "course_payment_instructions"
TEMPLATE_DIPLOMA_ENTRANCE_EXAM_SCHEDULE = "diploma_entrance_exam_schedule"
TEMPLATE_DIPLOMA_INTERVIEW_REQUEST = "diploma_interview_request"
TEMPLATE_DIPLOMA_DOCUMENT_UPLOAD_REQUEST = "diploma_document_upload_request"
TEMPLATE_APPLICATION_PAYMENT_PENDING = "application_payment_pending"
TEMPLATE_APPLICATION_SURVEY_REQUEST = "application_survey_request"

TEMPLATE_SLUGS = (
    TEMPLATE_REGISTRATION_RECEIVED,
    TEMPLATE_ENTRANCE_EXAM_INVITATION,
    TEMPLATE_ENTRANCE_EXAM_PASS,
    TEMPLATE_ENTRANCE_EXAM_FAIL,
    TEMPLATE_EXAM_INVITATION,
    TEMPLATE_ACCEPTANCE,
    TEMPLATE_REJECTION,
    TEMPLATE_ACTIVATION,
    TEMPLATE_PAYMENT_NOTIFICATION,
    TEMPLATE_COURSE_PAYMENT_INSTRUCTIONS,
    TEMPLATE_DIPLOMA_ENTRANCE_EXAM_SCHEDULE,
    TEMPLATE_DIPLOMA_INTERVIEW_REQUEST,
    TEMPLATE_DIPLOMA_DOCUMENT_UPLOAD_REQUEST,
    TEMPLATE_APPLICATION_PAYMENT_PENDING,
    TEMPLATE_APPLICATION_SURVEY_REQUEST,
)

RECIPIENT_SOURCE_LABELS = {
    "filter": _("Filter"),
    "search": _("Search"),
    "manual": _("Manual"),
}

EMAIL_STATUS_QUEUED = "queued"
EMAIL_STATUS_PROCESSING = "processing"
EMAIL_STATUS_SENT = "sent"
EMAIL_STATUS_FAILED = "failed"

EMAIL_STATUS_CHOICES = (
    (EMAIL_STATUS_QUEUED, _("Queued")),
    (EMAIL_STATUS_PROCESSING, _("Processing")),
    (EMAIL_STATUS_SENT, _("Sent")),
    (EMAIL_STATUS_FAILED, _("Failed")),
)

BULK_STATUS_DRAFT = "draft"
BULK_STATUS_SENT = "sent"
BULK_STATUS_PARTIAL = "partial"
BULK_STATUS_FAILED = "failed"

BULK_STATUS_CHOICES = (
    (BULK_STATUS_DRAFT, _("Draft")),
    (BULK_STATUS_SENT, _("Sent")),
    (BULK_STATUS_PARTIAL, _("Partial")),
    (BULK_STATUS_FAILED, _("Failed")),
)

REQUIRED_VARIABLES_BY_SLUG: dict[str, list[str]] = {
    TEMPLATE_REGISTRATION_RECEIVED: [],
    TEMPLATE_ENTRANCE_EXAM_INVITATION: [],
    TEMPLATE_ENTRANCE_EXAM_PASS: [],
    TEMPLATE_ENTRANCE_EXAM_FAIL: [],
    TEMPLATE_EXAM_INVITATION: [],
    TEMPLATE_ACCEPTANCE: [],
    TEMPLATE_REJECTION: [],
    TEMPLATE_ACTIVATION: [],
    TEMPLATE_PAYMENT_NOTIFICATION: [],
    TEMPLATE_COURSE_PAYMENT_INSTRUCTIONS: [],
    TEMPLATE_DIPLOMA_ENTRANCE_EXAM_SCHEDULE: [],
    TEMPLATE_DIPLOMA_INTERVIEW_REQUEST: [],
    TEMPLATE_APPLICATION_PAYMENT_PENDING: [],
}

ALL_VARIABLE_SAMPLES: dict[str, str] = {
    "full_name": "ماري جرجس",
    "email": "mary@example.com",
    "phone": "01234567890",
    "password": "1234567890",
    "national_id": "12345678901234",
    "date_of_birth": "١٥ مارس ٢٠٠٠",
    "gender": "أنثى",
    "education": "بكالوريوس",
    "church": "كنيسة السيدة العذراء",
    "priest_name": "القس يوحنا",
    "program_type": "دبلومة",
    "course_title": "مدخل إلى اللاهوت",
    "diploma_title": "دبلومة الدراسات اللاهوتية",
    "cohort_name": "دفعة ٢٠٢٦",
    "exam_title": "امتحان القبول للدبلومة",
    "exam_url": "https://example.com/exam/123",
    "exam_open_at": "١ يونيو ٢٠٢٦",
    "exam_close_at": "١٥ يونيو ٢٠٢٦",
    "language_code": "ar",
    "subject": "تم تقديم امتحان القبول",
    "rejection_reason": "لم يستوفِ متطلبات القبول",
    "activation_url": "https://example.com/activate/abc123",
    "phone_number": "01234567890",
    "amount": "500.00",
    "currency": "EGP",
    "due_at": "2026-06-30",
    "reference": "INV-001",
    "status": "مدفوع",
    "upload_url": "https://example.com/documents/upload/abc123",
    "staff_phone": "01234567890",
}

AVAILABLE_VARIABLES_BY_SLUG: dict[str, dict[str, list[str]]] = {
    TEMPLATE_REGISTRATION_RECEIVED: {
        "required": [],
        "optional": [
            "full_name",
            "national_id",
            "email",
            "phone",
            "password",
            "date_of_birth",
            "gender",
            "education",
            "church",
            "priest_name",
            "program_type",
            "course_title",
            "diploma_title",
        ],
    },
    TEMPLATE_ENTRANCE_EXAM_INVITATION: {
        "required": [],
        "optional": [
            "full_name",
            "email",
            "phone_number",
            "exam_title",
            "cohort_name",
            "exam_url",
            "exam_open_at",
            "exam_close_at",
        ],
    },
    TEMPLATE_ENTRANCE_EXAM_PASS: {
        "required": [],
        "optional": ["full_name", "exam_title", "cohort_name", "language_code", "subject"],
    },
    TEMPLATE_ENTRANCE_EXAM_FAIL: {
        "required": [],
        "optional": ["full_name", "exam_title", "cohort_name", "language_code", "subject"],
    },
    TEMPLATE_EXAM_INVITATION: {
        "required": [],
        "optional": ["full_name", "email", "exam_title", "exam_url", "exam_open_at", "exam_close_at"],
    },
    TEMPLATE_ACCEPTANCE: {
        "required": [],
        "optional": ["full_name", "cohort_name", "diploma_title", "program_type", "activation_url"],
    },
    TEMPLATE_REJECTION: {
        "required": [],
        "optional": ["full_name", "rejection_reason", "cohort_name"],
    },
    TEMPLATE_ACTIVATION: {
        "required": [],
        "optional": [
            "full_name",
            "activation_url",
            "email",
            "password",
            "phone",
            "cohort_name",
            "diploma_title",
        ],
    },
    TEMPLATE_PAYMENT_NOTIFICATION: {
        "required": [],
        "optional": ["full_name", "amount", "currency", "due_at", "reference", "status"],
    },
    TEMPLATE_COURSE_PAYMENT_INSTRUCTIONS: {
        "required": [],
        "optional": ["full_name", "course_title", "staff_phone"],
    },
    TEMPLATE_DIPLOMA_ENTRANCE_EXAM_SCHEDULE: {
        "required": [],
        "optional": [
            "full_name",
            "diploma_title",
            "cohort_name",
            "exam_title",
            "exam_open_at",
            "exam_close_at",
        ],
    },
    TEMPLATE_DIPLOMA_DOCUMENT_UPLOAD_REQUEST: {
        "required": ["full_name", "diploma_title", "cohort_name", "upload_url", "expires_at", "staff_phone"],
        "optional": [],
    },
    TEMPLATE_DIPLOMA_INTERVIEW_REQUEST: {
        "required": [],
        "optional": [
            "full_name",
            "diploma_title",
            "cohort_name",
            "interview_at",
            "phone_number",
            "expires_at",
        ],
    },
    TEMPLATE_APPLICATION_PAYMENT_PENDING: {
        "required": [],
        "optional": [
            "full_name",
            "program_type",
            "course_title",
            "diploma_title",
            "cohort_name",
            "staff_phone",
        ],
    },
    TEMPLATE_APPLICATION_SURVEY_REQUEST: {
        "required": ["full_name", "program_title", "form_title", "form_url", "expires_at", "staff_phone"],
        "optional": [],
    },
}

DESIGN_BRANDED = "branded"
DESIGN_MINIMAL = "minimal"
DESIGN_MODERN = "modern"
DESIGN_ELEGANT = "elegant"
DESIGN_CLEAN = "clean"

DESIGN_CHOICES = (
    (DESIGN_BRANDED, _("Branded")),
    (DESIGN_MINIMAL, _("Minimal")),
    (DESIGN_MODERN, _("Modern")),
    (DESIGN_ELEGANT, _("Elegant")),
    (DESIGN_CLEAN, _("Clean")),
)

SAMPLE_CONTEXT_BY_SLUG: dict[str, dict[str, str]] = {
    TEMPLATE_REGISTRATION_RECEIVED: {
        "full_name": "ماري جرجس",
        "email": "mary@example.com",
        "national_id": "12345678901234",
        "phone": "01234567890",
        "password": "1234567890",
    },
    TEMPLATE_ENTRANCE_EXAM_INVITATION: {
        "full_name": "ماري جرجس",
        "email": "mary@example.com",
        "phone_number": "01234567890",
        "exam_title": "امتحان القبول للدبلومة",
        "cohort_name": "دفعة ٢٠٢٦",
        "exam_url": "https://example.com/exam/123",
        "exam_open_at": "١ يونيو ٢٠٢٦",
        "exam_close_at": "١٥ يونيو ٢٠٢٦",
    },
    TEMPLATE_ENTRANCE_EXAM_PASS: {
        "full_name": "ماري جرجس",
        "exam_title": "امتحان القبول للدبلومة",
        "cohort_name": "دفعة ٢٠٢٦",
        "language_code": "ar",
        "subject": "تم تقديم امتحان القبول",
    },
    TEMPLATE_ENTRANCE_EXAM_FAIL: {
        "full_name": "ماري جرجس",
        "exam_title": "امتحان القبول للدبلومة",
        "cohort_name": "دفعة ٢٠٢٦",
        "language_code": "ar",
        "subject": "تم تقديم امتحان القبول",
    },
    TEMPLATE_EXAM_INVITATION: {
        "full_name": "ماري جرجس",
        "email": "mary@example.com",
        "exam_title": "امتحان القبول",
        "exam_url": "https://example.com/exam/123",
    },
    TEMPLATE_ACCEPTANCE: {
        "full_name": "ماري جرجس",
        "cohort_name": "دفعة ٢٠٢٦",
        "diploma_title": "دبلومة الدراسات اللاهوتية",
    },
    TEMPLATE_REJECTION: {
        "full_name": "ماري جرجس",
        "cohort_name": "دفعة ٢٠٢٦",
        "rejection_reason": "لم يستوفِ متطلبات القبول",
    },
    TEMPLATE_ACTIVATION: {
        "full_name": "ماري جرجس",
        "activation_url": "https://example.com/activate/abc123",
        "cohort_name": "دفعة ٢٠٢٦",
        "email": "mary@example.com",
        "password": "1234567890",
        "diploma_title": "دبلومة الدراسات اللاهوتية",
    },
    TEMPLATE_PAYMENT_NOTIFICATION: {
        "full_name": "ماري جرجس",
        "amount": "500.00",
        "currency": "EGP",
        "due_at": "2026-06-30",
        "reference": "INV-001",
        "status": "مدفوع",
    },
    TEMPLATE_COURSE_PAYMENT_INSTRUCTIONS: {
        "full_name": "ماري جرجس",
        "course_title": "مدخل إلى اللاهوت",
        "staff_phone": "01234567890",
    },
    TEMPLATE_DIPLOMA_ENTRANCE_EXAM_SCHEDULE: {
        "full_name": "ماري جرجس",
        "diploma_title": "دبلومة الدراسات اللاهوتية",
        "cohort_name": "دفعة ٢٠٢٦",
        "exam_title": "امتحان القبول للدبلومة",
        "exam_open_at": "١ يونيو ٢٠٢٦",
        "exam_close_at": "١٥ يونيو ٢٠٢٦",
    },
    TEMPLATE_DIPLOMA_INTERVIEW_REQUEST: {
        "full_name": "ماري جرجس",
        "diploma_title": "دبلومة الدراسات اللاهوتية",
        "cohort_name": "دفعة ٢٠٢٦",
        "interview_at": "2026-07-15T10:00:00+03:00",
        "phone_number": "01234567890",
        "expires_at": "2026-07-20T23:59:00+03:00",
    },
    TEMPLATE_APPLICATION_PAYMENT_PENDING: {
        "full_name": "ماري جرجس",
        "program_type": "دبلومة",
        "course_title": "",
        "diploma_title": "دبلومة الدراسات اللاهوتية",
        "cohort_name": "دفعة ٢٠٢٦",
        "staff_phone": "01234567890",
    },
    TEMPLATE_DIPLOMA_DOCUMENT_UPLOAD_REQUEST: {
        "full_name": "ماري جرجس",
        "diploma_title": "دبلومة الدراسات اللاهوتية",
        "cohort_name": "دفعة ٢٠٢٦",
        "upload_url": "https://example.com/documents/upload/abc123",
        "expires_at": "2026-07-20T23:59:00+03:00",
        "staff_phone": "01234567890",
    },
    TEMPLATE_APPLICATION_SURVEY_REQUEST: {
        "full_name": "ماري جرجس",
        "program_title": "دبلومة الدراسات اللاهوتية",
        "form_title": "استبيان القبول",
        "form_url": "https://example.com/intake/forms/fill/abc123",
        "expires_at": "2026-07-20T23:59:00+03:00",
        "staff_phone": "01234567890",
    },
}

TEMPLATE_SAMPLE_CONTEXT = SAMPLE_CONTEXT_BY_SLUG
