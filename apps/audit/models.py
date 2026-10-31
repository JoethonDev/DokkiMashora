from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

from .constants import (
    AUDIT_ACTION_CHOICES,
    AUDIT_SECTION_CHOICES,
    OUTCOME_CHOICES,
    OUTCOME_SUCCESS,
    SOURCE_CHOICES,
    SOURCE_USER,
)


class AuditEventQuerySet(models.QuerySet):
    """Append-only queryset.

    The model-level guard catches the normal ``instance.save()`` /
    ``instance.delete()`` paths. These overrides close the bulk ``update()`` and
    ``delete()`` paths that bypass ``Model.delete()``.
    """

    def update(self, **kwargs):
        # Django's SET_NULL collector updates ``actor_id`` when a user is
        # deleted. That is the only permitted mutation; history values remain
        # append-only.
        if set(kwargs) == {"actor_id"} and kwargs["actor_id"] is None:
            return super().update(**kwargs)
        raise PermissionError(_("Audit events are append-only and cannot be modified."))

    def delete(self):
        raise PermissionError(_("Audit events are append-only and cannot be deleted."))


class AuditEventManager(models.Manager.from_queryset(AuditEventQuerySet)):
    pass


class AuditEvent(TimeStampedModel):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
        verbose_name=_("actor"),
        db_index=False,
    )
    actor_label = models.CharField(
        _("actor name"),
        max_length=255,
        blank=True,
        help_text=_("Snapshot of the actor name at the time of the event."),
    )
    actor_role = models.CharField(
        _("actor role"),
        max_length=64,
        blank=True,
        help_text=_("Snapshot of the actor role at the time of the event."),
    )
    action = models.CharField(
        _("action"),
        max_length=64,
        choices=AUDIT_ACTION_CHOICES,
    )
    section = models.CharField(
        _("business section"),
        max_length=32,
        choices=AUDIT_SECTION_CHOICES,
        blank=True,
    )
    target_type = models.CharField(
        _("target type"),
        max_length=64,
        blank=True,
    )
    target_id = models.CharField(_("target ID"), max_length=64, blank=True)
    target_label = models.CharField(
        _("target name"),
        max_length=255,
        blank=True,
        help_text=_("Human-readable snapshot of the affected object."),
    )
    subject_type = models.CharField(
        _("subject type"),
        max_length=64,
        blank=True,
    )
    subject_id = models.CharField(_("subject ID"), max_length=64, blank=True)
    subject_label = models.CharField(
        _("subject name"),
        max_length=255,
        blank=True,
        help_text=_("Human-readable snapshot of the affected person, if any."),
    )
    source = models.CharField(
        _("source"),
        max_length=16,
        choices=SOURCE_CHOICES,
        default=SOURCE_USER,
    )
    outcome = models.CharField(
        _("outcome"),
        max_length=16,
        choices=OUTCOME_CHOICES,
        default=OUTCOME_SUCCESS,
        db_index=True,
    )
    correlation_id = models.CharField(
        _("correlation ID"),
        max_length=64,
        blank=True,
        db_index=True,
        help_text=_("Ties events from one request, email, or background job together."),
    )
    request_method = models.CharField(_("request method"), max_length=8, blank=True)
    request_path = models.CharField(_("request path"), max_length=255, blank=True)
    status_code = models.PositiveSmallIntegerField(
        _("HTTP status code"),
        null=True,
        blank=True,
    )
    metadata = models.JSONField(_("metadata"), default=dict, blank=True)
    search_text = models.TextField(
        _("search text"),
        blank=True,
        help_text=_("Lower-case aggregate used by the audit search."),
    )
    ip_address = models.GenericIPAddressField(_("IP address"), blank=True, null=True)

    objects = AuditEventManager()

    class Meta:
        verbose_name = _("audit event")
        verbose_name_plural = _("audit events")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["action", "created_at"]),
            models.Index(fields=["actor", "created_at"]),
            models.Index(fields=["target_type", "target_id"]),
            models.Index(fields=["section", "created_at"]),
            models.Index(fields=["source", "created_at"]),
            models.Index(fields=["subject_type", "subject_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.action} by {self.actor} at {self.created_at}"

    def delete(self, *args, **kwargs) -> tuple[int, dict[str, int]]:
        raise PermissionError(_("Audit events are append-only and cannot be deleted."))

    def save(self, *args, **kwargs) -> None:
        if self.pk is not None:
            raise PermissionError(_("Audit events are append-only and cannot be modified."))
        super().save(*args, **kwargs)
