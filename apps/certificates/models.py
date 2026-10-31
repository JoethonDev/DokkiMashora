import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.academics.models import Course, Diploma
from apps.core.models import TimeStampedModel
from apps.progression.constants import (
    CERTIFICATE_STATUS_AVAILABLE,
    CERTIFICATE_STATUS_CHOICES,
    CERTIFICATE_STATUS_GENERATED,
    CERTIFICATE_STATUS_REGENERATION,
)
from apps.students.models import StudentProfile

CERTIFICATE_LANGUAGE_CHOICES = (("ar", _("Arabic")), ("en", _("English")))
CERTIFICATE_TEMPLATE_TYPE_CHOICES = (("completion", _("Completion")),)

# P7-02: supported template source formats and their processing states.
TEMPLATE_SOURCE_FORMAT_NONE = ""
TEMPLATE_SOURCE_FORMAT_PDF = "pdf"
TEMPLATE_SOURCE_FORMAT_PSD = "psd"
TEMPLATE_SOURCE_FORMAT_JPG = "jpg"
TEMPLATE_SOURCE_FORMAT_CHOICES = (
    (TEMPLATE_SOURCE_FORMAT_NONE, _("None (HTML template)")),
    (TEMPLATE_SOURCE_FORMAT_PDF, _("PDF")),
    (TEMPLATE_SOURCE_FORMAT_PSD, _("PSD")),
    (TEMPLATE_SOURCE_FORMAT_JPG, _("JPG/JPEG")),
)

TEMPLATE_SOURCE_STATUS_NONE = "none"
TEMPLATE_SOURCE_STATUS_PENDING = "pending"
TEMPLATE_SOURCE_STATUS_READY = "ready"
TEMPLATE_SOURCE_STATUS_FAILED = "failed"
TEMPLATE_SOURCE_STATUS_CHOICES = (
    (TEMPLATE_SOURCE_STATUS_NONE, _("None")),
    (TEMPLATE_SOURCE_STATUS_PENDING, _("Processing")),
    (TEMPLATE_SOURCE_STATUS_READY, _("Ready")),
    (TEMPLATE_SOURCE_STATUS_FAILED, _("Failed")),
)

# Extensible dynamic-field registry (P7-03 v2). All registered keys are
# placeable in the editor; the registry remains the single allowlist for
# mapping validation and future field additions.
FIELD_KEY_STUDENT_NAME = "student_name"
FIELD_KEY_DATE = "date"
FIELD_KEY_CERTIFICATE_NUMBER = "certificate_number"
FIELD_KEY_DIPLOMA = "diploma"
FIELD_KEY_COURSE = "course"
FIELD_KEY_COURSE_VERSION = "course_version"
FIELD_KEY_SEMESTER = "semester"
FIELD_KEY_COHORT = "cohort"
FIELD_KEY_GRADUATION_DATE = "graduation_date"

MAPPING_FIELD_REGISTRY = (
    FIELD_KEY_STUDENT_NAME,
    FIELD_KEY_DATE,
    FIELD_KEY_CERTIFICATE_NUMBER,
    FIELD_KEY_DIPLOMA,
    FIELD_KEY_COURSE,
    FIELD_KEY_COURSE_VERSION,
    FIELD_KEY_SEMESTER,
    FIELD_KEY_COHORT,
    FIELD_KEY_GRADUATION_DATE,
)

# Display labels for the editor UI; the backend is the single source of truth.
FIELD_KEY_LABELS = {
    FIELD_KEY_STUDENT_NAME: _("Student Name"),
    FIELD_KEY_DATE: _("Date"),
    FIELD_KEY_CERTIFICATE_NUMBER: _("Certificate number"),
    FIELD_KEY_DIPLOMA: _("Diploma"),
    FIELD_KEY_COURSE: _("Course"),
    FIELD_KEY_COURSE_VERSION: _("Course version"),
    FIELD_KEY_SEMESTER: _("Semester"),
    FIELD_KEY_COHORT: _("Cohort"),
    FIELD_KEY_GRADUATION_DATE: _("Graduation date"),
}

# Allowlisted dynamic field keys for placement mappings (P7-02/P7-03).
ALLOWED_MAPPING_KEYS = MAPPING_FIELD_REGISTRY
# Layout schema version. v2 adds width/height (normalized 0..1) per field box;
# v1 mappings without width/height still load (renderer treats them as a
# zero-size point box preserving legacy placement).
MAPPING_SCHEMA_VERSION = 2

# No-code HTML certificate design (P7-04). The values are deliberately a small,
# explicit vocabulary: the server owns the markup and only these settings vary.
CERTIFICATE_DESIGN_SCHEMA_VERSION = 1
CERTIFICATE_LAYOUT_CHOICES = (
    ("classic", _("Classic frame")),
    ("modern", _("Modern bands")),
    ("minimal", _("Minimal")),
)
CERTIFICATE_BORDER_CHOICES = (
    ("none", _("No border")),
    ("thin", _("Thin frame")),
    ("bold", _("Bold frame")),
)
CERTIFICATE_TEXT_SIZE_CHOICES = (
    ("compact", _("Compact")),
    ("balanced", _("Balanced")),
    ("large", _("Large")),
)
CERTIFICATE_TEXT_ALIGN_CHOICES = (
    ("center", _("Centered")),
    ("start", _("Start aligned")),
)
CERTIFICATE_LOGO_SIZE_CHOICES = (
    ("small", _("Small")),
    ("medium", _("Medium")),
    ("large", _("Large")),
)
CERTIFICATE_DESIGN_DEFAULTS = {
    "layout": "classic",
    "accent_color": "#0F5132",
    "background_color": "#FFFDF7",
    "border_style": "bold",
    "text_size": "balanced",
    "text_align": "center",
    "show_program": True,
    "show_certificate_number": True,
    "show_completion_date": True,
    "show_issue_date": True,
    "show_cohort": True,
    "show_signature": False,
    "logo_size": "medium",
}


class CertificateCounter(models.Model):
    """Atomic counter for certificate number generation (prevents duplicates under concurrency)."""

    diploma = models.ForeignKey(
        Diploma,
        on_delete=models.CASCADE,
        related_name="certificate_counters",
        verbose_name=_("diploma"),
        null=True,
        blank=True,
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="certificate_counters",
        verbose_name=_("course"),
        null=True,
        blank=True,
    )
    year = models.PositiveIntegerField(_("year"))
    last_sequence = models.PositiveIntegerField(_("last sequence"), default=0)

    class Meta:
        verbose_name = _("certificate counter")
        verbose_name_plural = _("certificate counters")
        constraints = [
            models.UniqueConstraint(
                fields=("diploma", "year"),
                condition=models.Q(diploma__isnull=False),
                name="uq_certificate_counter_diploma_year",
            ),
            models.UniqueConstraint(
                fields=("course", "year"),
                condition=models.Q(course__isnull=False),
                name="uq_certificate_counter_course_year",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(diploma__isnull=False, course__isnull=True)
                    | models.Q(diploma__isnull=True, course__isnull=False)
                ),
                name="certificate_counter_exactly_one_program",
            ),
        ]

    def __str__(self):
        program = self.diploma or self.course
        return f"{program} - {self.year} ({self.last_sequence})"


class CertificateTemplate(TimeStampedModel):
    name = models.CharField(_("name"), max_length=255)
    diploma = models.ForeignKey(
        Diploma,
        on_delete=models.CASCADE,
        related_name="certificate_templates",
        verbose_name=_("diploma"),
        null=True,
        blank=True,
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="certificate_templates",
        verbose_name=_("course"),
        null=True,
        blank=True,
    )
    language = models.CharField(
        _("language"), max_length=10, default="ar", choices=CERTIFICATE_LANGUAGE_CHOICES,
    )
    template_type = models.CharField(
        _("template type"), max_length=50, default="completion", choices=CERTIFICATE_TEMPLATE_TYPE_CHOICES,
    )
    background_file = models.FileField(
        _("background file"),
        upload_to="certificate_templates/",
        null=True,
        blank=True,
    )
    body_html = models.TextField(_("body HTML"), blank=True)
    active = models.BooleanField(_("active"), default=False)
    is_placeholder = models.BooleanField(_("is placeholder"), default=True)
    # P7-02: protected source, preview, and validated field mapping. The source
    # is stored by the storage service under a generated key (never a direct
    # FileField path); preview is a protected PNG representation; mapping is a
    # versioned JSON placement plan consumed by the format adapters.
    source_format = models.CharField(
        _("source format"),
        max_length=10,
        choices=TEMPLATE_SOURCE_FORMAT_CHOICES,
        default=TEMPLATE_SOURCE_FORMAT_NONE,
        blank=True,
    )
    source_status = models.CharField(
        _("source status"),
        max_length=10,
        choices=TEMPLATE_SOURCE_STATUS_CHOICES,
        default=TEMPLATE_SOURCE_STATUS_NONE,
        blank=True,
    )
    source_storage_key = models.CharField(_("source storage key"), max_length=500, blank=True)
    source_byte_size = models.PositiveIntegerField(_("source size (bytes)"), null=True, blank=True)
    source_checksum = models.CharField(_("source checksum"), max_length=64, blank=True)
    source_error = models.TextField(_("source error"), blank=True)
    preview_storage_key = models.CharField(_("preview storage key"), max_length=500, blank=True)
    preview_width = models.PositiveIntegerField(_("preview width"), null=True, blank=True)
    preview_height = models.PositiveIntegerField(_("preview height"), null=True, blank=True)
    preview_page_count = models.PositiveIntegerField(_("preview page count"), default=1)
    field_mapping = models.JSONField(_("field mapping"), default=dict, blank=True)
    mapping_schema_version = models.PositiveIntegerField(_("mapping schema version"), default=0)
    # P7-04: no-code HTML design settings. The renderer owns the markup; this
    # JSON only stores bounded, validated design choices.
    design = models.JSONField(_("design"), default=dict, blank=True)
    logo_storage_key = models.CharField(_("logo storage key"), max_length=500, blank=True)
    logo_content_type = models.CharField(_("logo content type"), max_length=50, blank=True)
    logo_byte_size = models.PositiveIntegerField(_("logo size (bytes)"), null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_certificate_templates",
        verbose_name=_("created by"),
    )

    class Meta:
        verbose_name = _("certificate template")
        verbose_name_plural = _("certificate templates")
        constraints = [
            models.UniqueConstraint(
                fields=("diploma", "language", "template_type"),
                condition=models.Q(active=True, diploma__isnull=False),
                name="unique_active_template_per_diploma_language_type",
            ),
            models.UniqueConstraint(
                fields=("course", "language", "template_type"),
                condition=models.Q(active=True, course__isnull=False),
                name="unique_active_template_per_course_language_type",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(diploma__isnull=False, course__isnull=True)
                    | models.Q(diploma__isnull=True, course__isnull=False)
                ),
                name="certificate_template_exactly_one_program",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.language})"

    @property
    def program_title(self):
        if self.diploma_id:
            return self.diploma.title
        if self.course_id:
            return self.course.title
        return ""

    @property
    def has_legacy_html(self) -> bool:
        """Whether this template still relies on the old raw HTML body."""
        return bool(self.body_html and not self.design)

    def clean(self):
        if bool(self.diploma_id) == bool(self.course_id):
            raise ValidationError(
                _("Select exactly one program: a diploma or a course.")
            )
        # A visual design takes precedence over the legacy body. Do not block
        # saving the designer because an old custom body contains stale vars.
        if self.body_html and not self.design:
            known_vars = [
                "student_name", "diploma_title", "course_title", "course_version",
                "cohort_name", "completion_date", "certificate_number", "issue_date",
            ]
            found = re.findall(r"\{\{(\w+)\}\}", self.body_html)
            unknown = [v for v in found if v not in known_vars]
            if unknown:
                raise ValidationError(
                    _("Unknown template variables: %(vars)s") % {"vars": ", ".join(unknown)}
                )


class GeneratedCertificate(TimeStampedModel):
    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="generated_certificates",
        verbose_name=_("student"),
    )
    diploma_enrollment = models.ForeignKey(
        "academics.DiplomaEnrollment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_certificates",
        verbose_name=_("diploma enrollment"),
    )
    course_enrollment = models.ForeignKey(
        "academics.CourseEnrollment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_certificates",
        verbose_name=_("course enrollment"),
    )
    certificate_template = models.ForeignKey(
        CertificateTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_certificates",
        verbose_name=_("certificate template"),
    )
    certificate_number = models.CharField(
        _("certificate number"),
        max_length=100,
        unique=True,
        db_index=True,
    )
    status = models.CharField(
        _("status"),
        max_length=25,
        choices=CERTIFICATE_STATUS_CHOICES,
        default=CERTIFICATE_STATUS_GENERATED,
        db_index=True,
    )
    pdf_storage_key = models.CharField(_("PDF storage key"), max_length=500, blank=True)
    pdf_file_size = models.PositiveIntegerField(_("PDF file size"), null=True, blank=True)
    generated_at = models.DateTimeField(_("generated at"), auto_now_add=True)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_certificates",
        verbose_name=_("generated by"),
    )
    revoked_at = models.DateTimeField(_("revoked at"), null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="revoked_certificates",
        verbose_name=_("revoked by"),
    )
    revoke_reason = models.TextField(_("revoke reason"), blank=True)
    superseded_at = models.DateTimeField(_("superseded at"), null=True, blank=True)
    superseded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="superseded_certificates",
        verbose_name=_("superseded by"),
    )
    supersede_reason = models.TextField(_("supersede reason"), blank=True)
    metadata_snapshot = models.JSONField(_("metadata snapshot"), null=True, blank=True)

    class Meta:
        verbose_name = _("generated certificate")
        verbose_name_plural = _("generated certificates")
        ordering = ("-generated_at",)
        indexes = [
            models.Index(fields=["student"]),
            models.Index(fields=["diploma_enrollment"]),
            models.Index(fields=["course_enrollment"]),
            models.Index(fields=["status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["diploma_enrollment"],
                condition=models.Q(
                    diploma_enrollment__isnull=False,
                    status__in=[
                        CERTIFICATE_STATUS_GENERATED,
                        CERTIFICATE_STATUS_AVAILABLE,
                        CERTIFICATE_STATUS_REGENERATION,
                    ],
                ),
                name="uq_active_certificate_per_enrollment",
            ),
            models.UniqueConstraint(
                fields=["course_enrollment"],
                condition=models.Q(
                    course_enrollment__isnull=False,
                    status__in=[
                        CERTIFICATE_STATUS_GENERATED,
                        CERTIFICATE_STATUS_AVAILABLE,
                        CERTIFICATE_STATUS_REGENERATION,
                    ],
                ),
                name="uq_active_certificate_per_course_enrollment",
            ),
            models.CheckConstraint(
                condition=~(
                    models.Q(diploma_enrollment__isnull=False)
                    & models.Q(course_enrollment__isnull=False)
                ),
                name="certificate_at_most_one_program",
            ),
        ]

    def __str__(self):
        return f"{self.certificate_number} - {self.student}"

    @property
    def program_title(self):
        if self.diploma_enrollment_id:
            return self.diploma_enrollment.cohort.diploma.title
        if self.course_enrollment_id:
            return self.course_enrollment.course_version.course.title
        snapshot = self.metadata_snapshot or {}
        return (
            snapshot.get("program_title")
            or snapshot.get("diploma")
            or snapshot.get("course")
            or ""
        )
