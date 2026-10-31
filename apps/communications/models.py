"""Email template model."""

from __future__ import annotations

import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.template import Context, Template
from django.utils.translation import gettext_lazy as _

from apps.core.crypto import decrypt_secret, encrypt_secret
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
        for var in self.required_variables or []:
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

        Both subject and body use Django's template engine so bilingual
        ``{% if language_code == 'en' %}`` branches work. The subject is
        rendered without HTML autoescaping; the body is escaped exactly once at
        this boundary. Unknown variables render empty here; callers that need
        strict coverage checks should use ``variable_names()`` against the
        context before sending.
        """
        subject_out = Template(self.subject).render(Context(context, autoescape=False))
        body_out = Template(self.body).render(Context(context))
        return subject_out, body_out


class EmailFooter(models.Model):
    """Singleton global email footer appended to all outgoing emails."""

    body = models.TextField(
        _("body"),
        blank=True,
        default="",
        help_text=_("Appended to every outgoing email. Supports {{ variable }} placeholders."),
    )
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
    recipient_emails = models.TextField(
        _("recipient emails"), blank=True, help_text=_("One email per line, used when filter is empty")
    )
    resolved_recipients = models.JSONField(
        _("resolved recipients"),
        default=list,
        blank=True,
        help_text=_("Exact recipient list approved in preview"),
    )
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


class EmailConfiguration(TimeStampedModel):
    """Database-backed SMTP configuration.

    Environment settings remain the fallback when no row is active, so an
    existing deployment keeps working until IT saves and activates a row here.
    The password is encrypted at rest and never rendered back to the browser.
    """

    name = models.CharField(_("name"), max_length=120, default="Primary SMTP")
    host = models.CharField(_("SMTP host"), max_length=255)
    port = models.PositiveIntegerField(_("port"), default=587)
    username = models.CharField(_("username"), max_length=255, blank=True)
    password_encrypted = models.TextField(_("password"), blank=True, editable=False)
    use_tls = models.BooleanField(_("use TLS"), default=True)
    use_ssl = models.BooleanField(_("use SSL"), default=False)
    from_email = models.EmailField(_("sender email"), blank=True)
    from_name = models.CharField(_("sender name"), max_length=150, blank=True)
    timeout = models.PositiveIntegerField(_("timeout (seconds)"), default=20)
    is_active = models.BooleanField(_("active"), default=True, db_index=True)
    last_tested_at = models.DateTimeField(_("last tested at"), null=True, blank=True)
    last_test_ok = models.BooleanField(_("last test succeeded"), null=True, blank=True)
    last_test_error = models.TextField(_("last test error"), blank=True)

    class Meta:
        verbose_name = _("email configuration")
        verbose_name_plural = _("email configurations")
        ordering = ("-is_active", "name")
        constraints = [
            models.UniqueConstraint(
                fields=["is_active"],
                condition=models.Q(is_active=True),
                name="uq_email_configuration_active",
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.host}:{self.port})"

    @property
    def has_password(self) -> bool:
        return bool(self.password_encrypted)

    @property
    def sender_email(self) -> str:
        return self.from_email or settings.DEFAULT_FROM_EMAIL

    @property
    def sender(self) -> str:
        if self.from_name:
            return f"{self.from_name} <{self.sender_email}>"
        return self.sender_email

    def set_password(self, value: str) -> None:
        self.password_encrypted = encrypt_secret(value)

    def get_password(self) -> str:
        return decrypt_secret(self.password_encrypted)

    def clean(self) -> None:
        super().clean()
        if self.use_tls and self.use_ssl:
            raise ValidationError(_("Use either TLS or SSL, not both."))
        if not 1 <= int(self.port or 0) <= 65535:
            raise ValidationError({"port": _("Enter a valid port number.")})
        if "\r" in (self.from_name or "") or "\n" in (self.from_name or ""):
            raise ValidationError({"from_name": _("The sender name cannot contain line breaks.")})
        if "\r" in (self.from_email or "") or "\n" in (self.from_email or ""):
            raise ValidationError({"from_email": _("The sender email cannot contain line breaks.")})

    @classmethod
    def get_active(cls):
        return cls.objects.filter(is_active=True).first()
