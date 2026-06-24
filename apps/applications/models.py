"""Application model: tracks each registration request and its review status."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.students.models import StudentProfile

from .constants import (
    APP_STATUS_ACTIVATED,
    APP_STATUS_CHOICES,
    APP_STATUS_PENDING,
    APP_STATUS_REJECTED,
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_ACTIVATED,
    PROGRAM_APP_STATUS_CHOICES,
    PROGRAM_APP_STATUS_INVITED,
    PROGRAM_APP_STATUS_REJECTED,
    PROGRAM_APP_STATUS_SUBMITTED,
    PROGRAM_APP_STATUS_UNDER_REVIEW,
    PROGRAM_TYPE_CHOICES,
)

DECISION_ACTION_CHOICES = (
    ("mark_review", _("Marked under review")),
    ("approve", _("Approved")),
    ("reject", _("Rejected")),
    ("activate", _("Activated")),
    ("invite", _("Invited to entrance")),
    ("revert", _("Decision reverted")),
)

REVERTABLE_ACTIONS = frozenset({"mark_review", "approve", "reject", "invite"})


class Application(TimeStampedModel):
    """A registration request. A student may have multiple applications for different cohorts."""

    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="applications",
    )
    cohort = models.ForeignKey(
        "academics.Cohort",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="applications",
        verbose_name=_("cohort"),
    )
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=APP_STATUS_CHOICES,
        default=APP_STATUS_PENDING,
        db_index=True,
    )
    admin_notes = models.TextField(_("admin notes"), blank=True)
    rejection_reason = models.TextField(_("rejection reason"), blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_applications",
    )
    reviewed_at = models.DateTimeField(_("reviewed at"), null=True, blank=True)
    activated_at = models.DateTimeField(_("activated at"), null=True, blank=True)

    class Meta:
        verbose_name = _("application")
        verbose_name_plural = _("applications")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("status",)),
            models.Index(fields=("student", "status")),
        ]

    def __str__(self) -> str:
        return f"{self.student.full_name} - {self.get_status_display()}"

    @property
    def is_open(self) -> bool:
        return self.status not in {APP_STATUS_REJECTED, APP_STATUS_ACTIVATED}

    def mark_under_review(self, reviewer) -> None:
        self.status = "under_review"
        self.reviewed_by = reviewer
        self.save(update_fields=["status", "reviewed_by", "updated_at"])

    def approve(self, reviewer, notes: str = "") -> None:
        self.status = "approved"
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        if notes:
            self.admin_notes = notes
        self.save(update_fields=["status", "reviewed_by", "reviewed_at", "admin_notes", "updated_at"])

    def reject(self, reviewer, reason: str, notes: str = "") -> None:
        self.status = "rejected"
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

    def activate(self) -> None:
        self.status = "activated"
        self.activated_at = timezone.now()
        self.save(update_fields=["status", "activated_at", "updated_at"])

    @property
    def next_action(self) -> dict | None:
        """Return a row-level next action for the student, or None."""
        action_map = {
            "pending": {"text": _("Waiting for review"), "url": None},
            "under_review": {"text": _("Under review — no action needed"), "url": None},
            "approved": {"text": _("Awaiting activation"), "url": None},
            "activated": {"text": _("Enrolled — view courses"), "url": "students:dashboard"},
            "rejected": {"text": _("Apply to a different cohort"), "url": "students:reapply"},
        }
        return action_map.get(self.status)


class ProgramApplication(TimeStampedModel):
    """A child program request linked to a root Application/person identity.

    One person may have multiple ProgramApplication records (for different
    courses or diploma cohorts over time), but only one root Application.
    """

    root_application = models.ForeignKey(
        Application,
        on_delete=models.PROTECT,
        related_name="program_applications",
        verbose_name=_("root application"),
    )
    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.PROTECT,
        related_name="program_applications",
    )
    program_type = models.CharField(
        _("program type"),
        max_length=10,
        choices=PROGRAM_TYPE_CHOICES,
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
    activated_at = models.DateTimeField(_("activated at"), null=True, blank=True)

    class Meta:
        verbose_name = _("program application")
        verbose_name_plural = _("program applications")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("status",)),
            models.Index(fields=("student", "status")),
        ]

    def __str__(self) -> str:
        return f"{self.student.full_name} - {self.get_program_type_display()} - {self.get_status_display()}"

    @property
    def is_open(self) -> bool:
        return self.status not in {
            PROGRAM_APP_STATUS_REJECTED,
            PROGRAM_APP_STATUS_ACTIVATED,
        }

    def mark_under_review(self, reviewer) -> None:
        self.status = PROGRAM_APP_STATUS_UNDER_REVIEW
        self.reviewed_by = reviewer
        self.save(update_fields=["status", "reviewed_by", "updated_at"])

    def mark_invited(self, reviewer) -> None:
        self.status = PROGRAM_APP_STATUS_INVITED
        self.reviewed_by = reviewer
        self.save(update_fields=["status", "reviewed_by", "updated_at"])

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

    def activate(self) -> None:
        self.status = PROGRAM_APP_STATUS_ACTIVATED
        self.activated_at = timezone.now()
        self.save(update_fields=["status", "activated_at", "updated_at"])


EMAIL_ACTION_INVITATION = "entrance_exam_invitation"
EMAIL_ACTION_REJECTION = "rejection"

EMAIL_ACTION_CHOICES = (
    (EMAIL_ACTION_INVITATION, _("Entrance exam invitation")),
    (EMAIL_ACTION_REJECTION, _("Rejection")),
)


class ApplicationEmailLog(TimeStampedModel):
    """Tracks entrance exam invitation/rejection emails per application."""

    application = models.ForeignKey(
        Application, on_delete=models.CASCADE,
        related_name="email_logs",
    )
    cohort = models.ForeignKey(
        "academics.Cohort", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="application_email_logs",
        verbose_name=_("cohort"),
    )
    action = models.CharField(max_length=32, choices=EMAIL_ACTION_CHOICES, db_index=True)
    template_slug = models.CharField(max_length=64)
    recipient = models.EmailField()
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="application_email_logs",
    )
    status = models.CharField(max_length=8, default="sent")
    error = models.TextField(blank=True)
    is_resend = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("application email log")
        verbose_name_plural = _("application email logs")
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["application", "cohort", "action"],
                name="uq_appemail_once_per_action",
                condition=models.Q(status="sent") & models.Q(is_resend=False),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.application} - {self.get_action_display()} — {self.status}"


class ApplicationDecision(TimeStampedModel):
    """Historical record of every admin action on an Application or ProgramApplication."""

    TARGET_APPLICATION = "application"
    TARGET_PROGRAM = "program_application"

    target_type = models.CharField(max_length=32, db_index=True)
    target_id = models.CharField(max_length=32, db_index=True)
    action = models.CharField(max_length=24, choices=DECISION_ACTION_CHOICES)
    old_status = models.CharField(max_length=24)
    new_status = models.CharField(max_length=24)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="application_decisions",
    )
    reason = models.TextField(blank=True)

    class Meta:
        verbose_name = _("application decision")
        verbose_name_plural = _("application decisions")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("target_type", "target_id")),
        ]

    def __str__(self) -> str:
        return f"{self.get_action_display()} on {self.target_type}#{self.target_id} by {self.actor}"

    def delete(self, *args, **kwargs) -> tuple[int, dict[str, int]]:
        raise PermissionError("Application decisions are append-only.")

    def save(self, *args, **kwargs) -> None:
        if self.pk is not None:
            raise PermissionError("Application decisions are append-only.")
        super().save(*args, **kwargs)
