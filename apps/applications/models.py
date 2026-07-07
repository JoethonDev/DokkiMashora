"""Application model: tracks program applications only."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.students.models import StudentProfile

from .constants import (
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_ACTIVATED,
    PROGRAM_APP_STATUS_CHOICES,
    PROGRAM_APP_STATUS_REJECTED,
    PROGRAM_APP_STATUS_SUBMITTED,
    PROGRAM_TYPE_CHOICES,
    PROGRAM_TYPE_DIPLOMA,
)

DECISION_ACTION_CHOICES = (
    ("accept", _("Accepted")),
    ("reject", _("Rejected")),
    ("revert", _("Reverted")),
)

REVERTABLE_ACTIONS = frozenset({"accept", "reject"})


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
                condition=models.Q(program_type="diploma", status__in=("submitted", "under_review", "invited_to_entrance", "accepted", "activated")),
            ),
            models.UniqueConstraint(
                fields=["student", "course"],
                name="uq_program_app_course",
                condition=models.Q(program_type="course", status__in=("submitted", "under_review", "invited_to_entrance", "accepted", "activated")),
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


EMAIL_ACTION_INVITATION = "entrance_exam_invitation"
EMAIL_ACTION_REJECTION = "rejection"
EMAIL_ACTION_ACCEPTANCE = "acceptance"

EMAIL_ACTION_CHOICES = (
    (EMAIL_ACTION_INVITATION, _("Entrance exam invitation")),
    (EMAIL_ACTION_REJECTION, _("Rejection")),
    (EMAIL_ACTION_ACCEPTANCE, _("Acceptance")),
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

    def __str__(self) -> str:
        return f"{self.program_application} - {self.get_action_display()} — {self.status}"


class ProgramApplicationDecision(TimeStampedModel):
    """Historical record of every admin action on a ProgramApplication."""

    program_application = models.ForeignKey(
        ProgramApplication, on_delete=models.CASCADE,
        related_name="decisions",
    )
    action = models.CharField(max_length=24, choices=DECISION_ACTION_CHOICES)
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
