from __future__ import annotations

import hashlib

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

from .constants import (
    CURRENCY_CHOICES,
    CURRENCY_EGP,
    MATCH_METHOD_CHOICES,
    MATCH_METHOD_NONE,
    PAYMENT_STATUS_CHOICES,
    PAYMENT_STATUS_UNPAID,
    ROW_STATUS_CHOICES,
    ROW_STATUS_PARSED,
    UPLOAD_STATUS_CHOICES,
    UPLOAD_STATUS_PENDING,
)


class PaymentUpload(TimeStampedModel):
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="payment_uploads",
        verbose_name=_("uploaded by"),
    )
    original_filename = models.CharField(_("original filename"), max_length=255)
    file_hash = models.CharField(_("file hash"), max_length=64, blank=True)
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=UPLOAD_STATUS_CHOICES,
        default=UPLOAD_STATUS_PENDING,
        db_index=True,
    )
    total_rows = models.IntegerField(_("total rows"), default=0)
    matched_rows = models.IntegerField(_("matched rows"), default=0)
    unmatched_rows = models.IntegerField(_("unmatched rows"), default=0)
    created_rows = models.IntegerField(_("created rows"), default=0)
    updated_rows = models.IntegerField(_("updated rows"), default=0)
    failed_rows = models.IntegerField(_("failed rows"), default=0)
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)
    error_summary = models.TextField(_("error summary"), blank=True)

    class Meta:
        verbose_name = _("payment upload")
        verbose_name_plural = _("payment uploads")
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.original_filename} ({self.get_status_display()})"

    def compute_hash(self, content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()


class PaymentUploadRow(TimeStampedModel):
    upload = models.ForeignKey(
        PaymentUpload,
        on_delete=models.CASCADE,
        related_name="rows",
        verbose_name=_("upload"),
    )
    row_number = models.IntegerField(_("row number"))
    raw_data = models.JSONField(_("raw data"), default=dict, blank=True)
    normalized_national_id = models.CharField(
        _("normalized national ID"), max_length=32, blank=True
    )
    normalized_email = models.EmailField(_("normalized email"), max_length=254, blank=True)
    normalized_phone = models.CharField(_("normalized phone"), max_length=32, blank=True)
    amount = models.DecimalField(
        _("amount"), max_digits=12, decimal_places=2, null=True, blank=True
    )
    currency = models.CharField(
        _("currency"), max_length=3, choices=CURRENCY_CHOICES, default=CURRENCY_EGP
    )
    paid_at = models.DateTimeField(_("paid at"), null=True, blank=True)
    external_reference = models.CharField(
        _("external reference"), max_length=255, blank=True
    )
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=ROW_STATUS_CHOICES,
        default=ROW_STATUS_PARSED,
        db_index=True,
    )
    match_method = models.CharField(
        _("match method"),
        max_length=16,
        choices=MATCH_METHOD_CHOICES,
        default=MATCH_METHOD_NONE,
        blank=True,
    )
    matched_student = models.ForeignKey(
        "students.StudentProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_rows",
        verbose_name=_("matched student"),
    )
    matched_application = models.ForeignKey(
        "applications.Application",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_rows",
        verbose_name=_("matched application"),
    )
    matched_diploma_enrollment = models.ForeignKey(
        "academics.DiplomaEnrollment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_rows",
        verbose_name=_("matched diploma enrollment"),
    )
    matched_course_enrollment = models.ForeignKey(
        "academics.CourseEnrollment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_rows",
        verbose_name=_("matched course enrollment"),
    )
    error_message = models.TextField(_("error message"), blank=True)

    class Meta:
        verbose_name = _("payment upload row")
        verbose_name_plural = _("payment upload rows")
        ordering = ("upload", "row_number")
        unique_together = (("upload", "row_number"),)

    def __str__(self) -> str:
        return f"Row {self.row_number} — {self.get_status_display()}"


class PaymentRecord(TimeStampedModel):
    student = models.ForeignKey(
        "students.StudentProfile",
        on_delete=models.CASCADE,
        related_name="payment_records",
        verbose_name=_("student"),
        db_index=True,
    )
    application = models.ForeignKey(
        "applications.Application",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_records",
        verbose_name=_("application"),
    )
    upload_row = models.ForeignKey(
        PaymentUploadRow,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_records",
        verbose_name=_("upload row"),
    )
    external_reference = models.CharField(
        _("external reference"), max_length=255, blank=True, db_index=True
    )
    amount = models.DecimalField(_("amount"), max_digits=12, decimal_places=2)
    currency = models.CharField(
        _("currency"), max_length=3, choices=CURRENCY_CHOICES, default=CURRENCY_EGP
    )
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=PAYMENT_STATUS_CHOICES,
        default=PAYMENT_STATUS_UNPAID,
        db_index=True,
    )
    paid_at = models.DateTimeField(_("paid at"), null=True, blank=True)
    due_at = models.DateTimeField(_("due at"), null=True, blank=True, db_index=True)
    diploma_enrollment = models.ForeignKey(
        "academics.DiplomaEnrollment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_records",
        verbose_name=_("diploma enrollment"),
    )
    course_enrollment = models.ForeignKey(
        "academics.CourseEnrollment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_records",
        verbose_name=_("course enrollment"),
    )
    notes = models.TextField(_("notes"), blank=True)
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="imported_payments",
        verbose_name=_("imported by"),
    )

    class Meta:
        verbose_name = _("payment record")
        verbose_name_plural = _("payment records")
        ordering = ("-paid_at", "-created_at")
        indexes = [
            models.Index(fields=["student", "status"]),
            models.Index(fields=["external_reference", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["external_reference"],
                name="unique_external_reference_nonempty",
                condition=models.Q(external_reference__gt=""),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.student} — {self.amount} {self.currency} ({self.get_status_display()})"
