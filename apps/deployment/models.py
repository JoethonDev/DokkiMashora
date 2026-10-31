"""Operational history for Celery jobs."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

JOB_STATUS_STARTED = "started"
JOB_STATUS_SUCCESS = "success"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_RETRIED = "retried"

JOB_STATUS_CHOICES = (
    (JOB_STATUS_STARTED, _("Started")),
    (JOB_STATUS_SUCCESS, _("Succeeded")),
    (JOB_STATUS_FAILED, _("Failed")),
    (JOB_STATUS_RETRIED, _("Retried")),
)


class SystemJobRun(TimeStampedModel):
    """One Celery task execution, updated in place by task signals."""

    task_id = models.CharField(_("task ID"), max_length=64, unique=True)
    task_name = models.CharField(_("task name"), max_length=255)
    queue = models.CharField(_("queue"), max_length=64, blank=True)
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=JOB_STATUS_CHOICES,
        default=JOB_STATUS_STARTED,
    )
    started_at = models.DateTimeField(_("started at"), default=None, null=True, blank=True)
    finished_at = models.DateTimeField(_("finished at"), null=True, blank=True)
    duration_ms = models.PositiveIntegerField(_("duration (ms)"), null=True, blank=True)
    args_summary = models.TextField(_("arguments summary"), blank=True)
    result_summary = models.TextField(_("result summary"), blank=True)
    error = models.TextField(_("error"), blank=True)
    correlation_id = models.CharField(_("correlation ID"), max_length=64, blank=True)

    class Meta:
        verbose_name = _("system job run")
        verbose_name_plural = _("system job runs")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["status", "started_at"]),
            models.Index(fields=["task_name", "started_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.task_name} ({self.get_status_display()})"
