"""Email template model."""

from __future__ import annotations

import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.template import Context, Template
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

from .constants import (
    BULK_STATUS_CHOICES,
    BULK_STATUS_DRAFT,
    DESIGN_BRANDED,
    DESIGN_CHOICES,
    TEMPLATE_SLUGS,
)


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
    is_enabled_for_action = models.BooleanField(_("enabled for action"), default=True)
    design = models.CharField(_("design"), max_length=16, choices=DESIGN_CHOICES, default=DESIGN_BRANDED)
    required_variables = models.JSONField(
        _("required variables"),
        default=list,
        blank=True,
        help_text=_("Variables that must appear in the body. Editable in the template editor."),
    )

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
        for var in (self.required_variables or []):
            if f"{{{{ {var} }}}}" not in self.body:
                raise ValidationError(
                    _("Body must contain the required variable {{ %(var)s }}.") % {"var": var}
                )
        if self.slug and EmailTemplate.objects.filter(slug=self.slug).exclude(pk=self.pk).exists():
            raise ValidationError({"slug": _("A template for this action already exists.")})

    def variable_names(self) -> list[str]:
        return sorted(set(re.findall(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}", self.body)))

    def render(self, context: dict[str, str]) -> tuple[str, str]:
        """Return (rendered_subject, rendered_body) for the given context.

        The subject is plain text and is token-replaced only. The body is
        evaluated exactly once through Django's template engine so conditional
        {% if %} blocks render and every variable value is HTML-escaped at
        this boundary. Unknown variables render empty here; callers that need
        strict coverage checks should use ``variable_names()`` against the
        context before sending.
        """
        subject_out = self.subject
        for key, value in context.items():
            token = "{{ " + str(key) + " }}"
            subject_out = subject_out.replace(token, str(value))
        body_out = Template(self.body).render(Context(context))
        return subject_out, body_out


class EmailFooter(models.Model):
    """Singleton global email footer appended to all outgoing emails."""

    body = models.TextField(_("body"), blank=True, default="",
                            help_text=_("Appended to every outgoing email. Supports {{ variable }} placeholders."))
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("email footer")
        verbose_name_plural = _("email footer")

    def __str__(self) -> str:
        return _("Global email footer")


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
    resolved_recipients = models.JSONField(_("resolved recipients"), default=list, blank=True, help_text=_("Exact recipient list approved in preview"))
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
