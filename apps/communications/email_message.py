"""Email message log model."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

from .constants import EMAIL_STATUS_CHOICES, EMAIL_STATUS_QUEUED


class EmailMessage(TimeStampedModel):
    """A log entry for a sent (or attempted) email."""

    template_slug = models.CharField(_("template slug"), max_length=64, blank=True)
    to_email = models.EmailField(_("to email"), db_index=True)
    cc_emails = models.TextField(_("cc emails"), blank=True)
    subject = models.CharField(_("subject"), max_length=255)
    body = models.TextField(_("body"))
    status = models.CharField(
        _("status"),
        max_length=8,
        choices=EMAIL_STATUS_CHOICES,
        default=EMAIL_STATUS_QUEUED,
        db_index=True,
    )
    error = models.TextField(_("error"), blank=True)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_emails",
    )
    sent_at = models.DateTimeField(_("sent at"), null=True, blank=True)

    class Meta:
        verbose_name = _("email message")
        verbose_name_plural = _("email messages")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("to_email",)),
            models.Index(fields=("status",)),
        ]

    def __str__(self) -> str:
        return f"{self.to_email} - {self.subject[:40]}"
