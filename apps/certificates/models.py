import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.academics.models import Diploma
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
TEMPLATE_SOURCE_FORMAT_CHOICES = (
    (TEMPLATE_SOURCE_FORMAT_NONE, _("None (HTML template)")),
    (TEMPLATE_SOURCE_FORMAT_PDF, _("PDF")),
    (TEMPLATE_SOURCE_FORMAT_PSD, _("PSD")),
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

# Allowlisted dynamic field keys for placement mappings (P7-02/P7-03).
ALLOWED_MAPPING_KEYS = ("student_name",)
MAPPING_SCHEMA_VERSION = 1


class CertificateCounter(models.Model):
    """Atomic counter for certificate number generation (prevents duplicates under concurrency)."""

    diploma = models.ForeignKey(
        Diploma,
        on_delete=models.CASCADE,
        related_name="certificate_counters",
        verbose_name=_("diploma"),
    )
    year = models.PositiveIntegerField(_("year"))
    last_sequence = models.PositiveIntegerField(_("last sequence"), default=0)

    class Meta:
        verbose_name = _("certificate counter")
        verbose_name_plural = _("certificate counters")
        unique_together = (("diploma", "year"),)

    def __str__(self):
        return f"{self.diploma.title} - {self.year} ({self.last_sequence})"


class CertificateTemplate(TimeStampedModel):
    name = models.CharField(_("name"), max_length=255)
    diploma = models.ForeignKey(
        Diploma,
        on_delete=models.CASCADE,
        related_name="certificate_templates",
        verbose_name=_("diploma"),
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
                condition=models.Q(active=True),
                name="unique_active_template_per_diploma_language_type",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.language})"

    def clean(self):
        known_vars = [
            "student_name", "diploma_title", "cohort_name",
            "completion_date", "certificate_number", "issue_date",
        ]
        if self.body_html:
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
    metadata_snapshot = models.JSONField(_("metadata snapshot"), null=True, blank=True)

    class Meta:
        verbose_name = _("generated certificate")
        verbose_name_plural = _("generated certificates")
        ordering = ("-generated_at",)
        indexes = [
            models.Index(fields=["student"]),
            models.Index(fields=["diploma_enrollment"]),
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
        ]

    def __str__(self):
        return f"{self.certificate_number} - {self.student}"
