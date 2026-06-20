"""Email template model."""

from __future__ import annotations

import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

from .constants import BULK_STATUS_CHOICES, BULK_STATUS_DRAFT, TEMPLATE_SLUGS


class EmailTemplate(TimeStampedModel):
    """A reusable email template with simple {{ variable }} placeholders."""

    slug = models.CharField(
        _("slug"),
        max_length=64,
        choices=[(s, s) for s in TEMPLATE_SLUGS],
        unique=True,
    )
    name = models.CharField(_("name"), max_length=120)
    subject = models.CharField(_("subject"), max_length=255)
    body = models.TextField(_("body"))
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("email template")
        verbose_name_plural = _("email templates")
        ordering = ("slug",)

    def __str__(self) -> str:
        return f"{self.name} ({self.slug})"

    def clean(self) -> None:
        super().clean()
        if self.slug and self.slug not in TEMPLATE_SLUGS:
            raise ValidationError({"slug": _("Unknown template slug.")})

    def variable_names(self) -> list[str]:
        return sorted(set(re.findall(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}", self.body)))

    def render(self, context: dict[str, str]) -> tuple[str, str]:
        """Return (rendered_subject, rendered_body) for the given context."""
        subject_out = self.subject
        body_out = self.body
        for key, value in context.items():
            token = "{{ " + str(key) + " }}"
            subject_out = subject_out.replace(token, str(value))
            body_out = body_out.replace(token, str(value))
        return subject_out, body_out


class BulkEmailSend(TimeStampedModel):
    """A bulk email send operation."""

    subject = models.CharField(_("subject"), max_length=255)
    body = models.TextField(_("body"))
    template = models.ForeignKey(
        EmailTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bulk_sends",
    )
    recipient_filter = models.JSONField(_("recipient filter"), default=dict, blank=True)
    recipient_emails = models.TextField(_("recipient emails"), blank=True, help_text=_("One email per line, used when filter is empty"))
    recipient_count = models.IntegerField(_("recipient count"), default=0)
    success_count = models.IntegerField(_("success count"), default=0)
    failure_count = models.IntegerField(_("failure count"), default=0)
    status = models.CharField(
        _("status"),
        max_length=8,
        choices=BULK_STATUS_CHOICES,
        default=BULK_STATUS_DRAFT,
        db_index=True,
    )
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bulk_email_sends",
    )
    sent_at = models.DateTimeField(_("sent at"), null=True, blank=True)

    class Meta:
        verbose_name = _("bulk email send")
        verbose_name_plural = _("bulk email sends")
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.subject[:60]} ({self.get_status_display()})"
