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
)


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
