"""Application model: tracks program applications only."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.students.models import StudentProfile

from .constants import (
    ACTIVE_PROGRAM_APP_STATUSES,
    DECISION_ACTION_CHOICES,
    DOCUMENT_REQUEST_STATUS_CHOICES,
    DOCUMENT_REQUEST_STATUS_REQUESTED,
    DOCUMENT_REQUEST_STATUS_SUBMITTED,
    EMAIL_ACTION_CHOICES,
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_ACTIVATED,
    PROGRAM_APP_STATUS_CHOICES,
    PROGRAM_APP_STATUS_REJECTED,
    PROGRAM_APP_STATUS_SUBMITTED,
    PROGRAM_TYPE_CHOICES,
    PROGRAM_TYPE_DIPLOMA,
)


class ProgramApplication(TimeStampedModel):
    """A program request for a diploma cohort or standalone course."""

    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.PROTECT,
        related_name="program_applications",
    )
    program_type = models.CharField(
        _("program type"),
        max_length=10,
        choices=PROGRAM_TYPE_CHOICES,
        default=PROGRAM_TYPE_DIPLOMA,
    )
    cohort = models.ForeignKey(
        "academics.Cohort",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="program_applications",
        verbose_name=_("cohort"),
    )
    course = models.ForeignKey(
        "academics.Course",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="program_applications",
        verbose_name=_("course"),
    )
    status = models.CharField(
        _("status"),
        max_length=24,
        choices=PROGRAM_APP_STATUS_CHOICES,
        default=PROGRAM_APP_STATUS_SUBMITTED,
        db_index=True,
    )
    admin_notes = models.TextField(_("admin notes"), blank=True)
    rejection_reason = models.TextField(_("rejection reason"), blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_program_applications",
    )
    reviewed_at = models.DateTimeField(_("reviewed at"), null=True, blank=True)

    class Meta:
        verbose_name = _("program application")
        verbose_name_plural = _("program applications")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("status",)),
            models.Index(fields=("student", "status")),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(program_type="diploma", cohort__isnull=False, course__isnull=True)
                    | models.Q(program_type="course", course__isnull=False, cohort__isnull=True)
                    | models.Q(cohort__isnull=True, course__isnull=True)
                ),
                name="ck_program_app_target",
            ),
            models.UniqueConstraint(
                fields=["student", "cohort"],
                name="uq_program_app_diploma",
                condition=models.Q(program_type="diploma", status__in=ACTIVE_PROGRAM_APP_STATUSES),
            ),
            models.UniqueConstraint(
                fields=["student", "course"],
                name="uq_program_app_course",
                condition=models.Q(program_type="course", status__in=ACTIVE_PROGRAM_APP_STATUSES),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.student.full_name} - {self.get_program_type_display()} - {self.get_status_display()}"

    @property
    def is_open(self) -> bool:
        return self.status not in {
            PROGRAM_APP_STATUS_REJECTED,
            PROGRAM_APP_STATUS_ACTIVATED,
        }

    @property
    def submitted_document_upload(self) -> ApplicationDocumentUpload | None:
        # ponytail: requires prefetch_related("document_requests__upload") — N+1 if called without it
        for req in self.document_requests.all():
            if req.status == DOCUMENT_REQUEST_STATUS_SUBMITTED and hasattr(req, "upload_id") and req.upload_id:
                return req.upload
        return None

    def accept(self, reviewer, notes: str = "") -> None:
        self.status = PROGRAM_APP_STATUS_ACCEPTED
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        if notes:
            self.admin_notes = notes
        self.save(
            update_fields=["status", "reviewed_by", "reviewed_at", "admin_notes", "updated_at"]
        )

    def reject(self, reviewer, reason: str, notes: str = "") -> None:
        self.status = PROGRAM_APP_STATUS_REJECTED
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.rejection_reason = reason
        if notes:
            self.admin_notes = notes
        self.save(
            update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "rejection_reason",
                "admin_notes",
                "updated_at",
            ]
        )


class ProgramApplicationEmailLog(TimeStampedModel):
    """Tracks emails sent for a program application."""

    program_application = models.ForeignKey(
        ProgramApplication, on_delete=models.CASCADE,
        related_name="email_logs",
    )
    action = models.CharField(max_length=32, choices=EMAIL_ACTION_CHOICES, db_index=True)
    template_slug = models.CharField(max_length=64)
    recipient = models.EmailField()
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="program_application_email_logs",
    )
    status = models.CharField(max_length=8, default="sent")
    error = models.TextField(blank=True)
    is_resend = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("program application email log")
        verbose_name_plural = _("program application email logs")
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["program_application", "action"],
                name="uq_program_app_email_log_sent",
                condition=models.Q(status="sent", is_resend=False),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.program_application} - {self.get_action_display()} — {self.status}"


class ProgramApplicationDecision(TimeStampedModel):
    """Historical record of every admin action on a ProgramApplication."""

    program_application = models.ForeignKey(
        ProgramApplication, on_delete=models.CASCADE,
        related_name="decisions",
    )
    action = models.CharField(max_length=32, choices=DECISION_ACTION_CHOICES)
    old_status = models.CharField(max_length=24)
    new_status = models.CharField(max_length=24)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="program_application_decisions",
    )
    reason = models.TextField(blank=True)
    metadata = models.JSONField(_("metadata"), default=dict, blank=True)

    class Meta:
        verbose_name = _("program application decision")
        verbose_name_plural = _("program application decisions")
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.get_action_display()} on {self.program_application} by {self.actor}"

    def delete(self, *args, **kwargs) -> tuple[int, dict[str, int]]:
        raise PermissionError("Program application decisions are append-only.")

    def save(self, *args, **kwargs) -> None:
        if self.pk is not None:
            raise PermissionError("Program application decisions are append-only.")
        super().save(*args, **kwargs)


class ProgramApplicationNote(TimeStampedModel):
    """Append-only admin notes for a ProgramApplication."""

    program_application = models.ForeignKey(
        ProgramApplication, on_delete=models.CASCADE,
        related_name="notes",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="program_application_notes",
    )
    note = models.TextField(_("note"))
    metadata = models.JSONField(_("metadata"), default=dict, blank=True)

    class Meta:
        verbose_name = _("program application note")
        verbose_name_plural = _("program application notes")
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"Note on {self.program_application} by {self.actor}"

    def delete(self, *args, **kwargs) -> tuple[int, dict[str, int]]:
        raise PermissionError("Program application notes are append-only.")

    def save(self, *args, **kwargs) -> None:
        if self.pk is not None:
            raise PermissionError("Program application notes are append-only.")
        super().save(*args, **kwargs)



class ApplicationDocumentRequest(TimeStampedModel):
    """An expiring document upload request for an accepted diploma application."""

    program_application = models.ForeignKey(
        ProgramApplication, on_delete=models.CASCADE,
        related_name="document_requests",
    )
    status = models.CharField(
        _("status"),
        max_length=12,
        choices=DOCUMENT_REQUEST_STATUS_CHOICES,
        default=DOCUMENT_REQUEST_STATUS_REQUESTED,
        db_index=True,
    )
    token_hash = models.CharField(
        _("token hash"), max_length=64, unique=True, db_index=True,
    )
    expires_at = models.DateTimeField(_("expires at"), db_index=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="document_requests_made",
    )
    requested_at = models.DateTimeField(_("requested at"), default=timezone.now)
    submitted_at = models.DateTimeField(_("submitted at"), null=True, blank=True)
    cancelled_at = models.DateTimeField(_("cancelled at"), null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="document_requests_cancelled",
    )
    metadata = models.JSONField(_("metadata"), default=dict, blank=True)

    class Meta:
        verbose_name = _("application document request")
        verbose_name_plural = _("application document requests")
        ordering = ("-requested_at",)
        indexes = [
            models.Index(fields=("program_application", "status")),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["program_application"],
                name="uq_one_open_doc_request",
                condition=models.Q(status="requested"),
            ),
        ]

    def __str__(self):
        return f"DocRequest {self.pk} - {self.get_status_display()}"


class ApplicationDocumentUpload(TimeStampedModel):
    """Uploaded persona image and combined attachment PDF for a document request."""

    request = models.OneToOneField(
        ApplicationDocumentRequest, on_delete=models.CASCADE,
        related_name="upload",
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="document_uploads",
    )
    submitted_at = models.DateTimeField(_("submitted at"), default=timezone.now)

    persona_image_storage_key = models.CharField(_("persona image key"), max_length=512)
    persona_image_original_filename = models.CharField(_("original filename"), max_length=255)
    persona_image_content_type = models.CharField(_("content type"), max_length=64)
    persona_image_file_size = models.PositiveIntegerField(_("file size"))
    persona_image_checksum = models.CharField(_("checksum"), max_length=64)

    attachments_pdf_storage_key = models.CharField(_("attachments PDF key"), max_length=512)
    attachments_original_filenames = models.JSONField(_("original filenames"), default=list)
    attachments_content_type = models.CharField(
        _("content type"), max_length=64, default="application/pdf",
    )
    attachments_file_size = models.PositiveIntegerField(_("file size"))
    attachments_checksum = models.CharField(_("checksum"), max_length=64)
    original_file_count = models.PositiveSmallIntegerField(_("original file count"))

    class Meta:
        verbose_name = _("application document upload")
        verbose_name_plural = _("application document uploads")

    def __str__(self):
        return f"DocUpload for request {self.request_id}"


# Legacy email action constants (re-exported from constants.py)
from .constants import (  # noqa: E402, F401, I001
    DOCUMENT_REQUEST_STATUS_REQUESTED,
    DOCUMENT_REQUEST_STATUS_EXPIRED,
    DOCUMENT_REQUEST_STATUS_CANCELLED,
    EMAIL_ACTION_ACCEPTANCE,
    EMAIL_ACTION_ENTRANCE_EXAM_INVITATION,
    EMAIL_ACTION_ENTRANCE_EXAM_INVITATION as EMAIL_ACTION_INVITATION,
    EMAIL_ACTION_EXAM_RESULT,
    EMAIL_ACTION_NOTE,
    EMAIL_ACTION_REJECTION,
)
