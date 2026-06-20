from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

from .constants import AUDIT_ACTION_CHOICES


class AuditEvent(TimeStampedModel):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
        verbose_name=_("actor"),
        db_index=True,
    )
    action = models.CharField(
        _("action"),
        max_length=64,
        choices=AUDIT_ACTION_CHOICES,
        db_index=True,
    )
    target_type = models.CharField(
        _("target type"),
        max_length=64,
        blank=True,
        db_index=True,
    )
    target_id = models.CharField(_("target ID"), max_length=64, blank=True)
    metadata = models.JSONField(_("metadata"), default=dict, blank=True)
    ip_address = models.GenericIPAddressField(
        _("IP address"), blank=True, null=True
    )

    class Meta:
        verbose_name = _("audit event")
        verbose_name_plural = _("audit events")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["action", "created_at"]),
            models.Index(fields=["actor", "created_at"]),
            models.Index(fields=["target_type", "target_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_action_display()} by {self.actor} at {self.created_at}"

    def delete(self, *args, **kwargs) -> tuple[int, dict[str, int]]:
        raise PermissionError("Audit events are append-only and cannot be deleted.")

    def save(self, *args, **kwargs) -> None:
        if self.pk is not None:
            raise PermissionError("Audit events are append-only and cannot be modified.")
        super().save(*args, **kwargs)
