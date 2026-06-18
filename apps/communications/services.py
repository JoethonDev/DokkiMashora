"""Email sending service and template rendering helpers."""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.core.mail import EmailMessage as DjangoEmailMessage
from django.core.mail import get_connection
from django.utils import timezone

from .constants import EMAIL_STATUS_FAILED, EMAIL_STATUS_SENT
from .email_message import EmailMessage
from .models import EmailTemplate

logger = logging.getLogger("apps.communications")


def render_template(slug: str, context: dict[str, Any]) -> tuple[str, str]:
    """Render an EmailTemplate by slug, returning (subject, body)."""
    template = EmailTemplate.objects.get(slug=slug, is_active=True)
    return template.render(context)


def send_templated_email(
    slug: str,
    to_email: str,
    context: dict[str, Any],
    *,
    sent_by=None,
    cc: list[str] | None = None,
) -> EmailMessage:
    """Render a template, send via SMTP, and record the result."""
    template = EmailTemplate.objects.get(slug=slug, is_active=True)
    subject, body = template.render(context)
    return send_email(
        template_slug=slug,
        to_email=to_email,
        subject=subject,
        body=body,
        sent_by=sent_by,
        cc=cc or [],
    )


def send_email(
    *,
    template_slug: str,
    to_email: str,
    subject: str,
    body: str,
    sent_by=None,
    cc: list[str] | None = None,
) -> EmailMessage:
    """Send an email and record the result in EmailMessage."""
    cc = cc or []
    record = EmailMessage.objects.create(
        template_slug=template_slug,
        to_email=to_email,
        cc_emails=", ".join(cc),
        subject=subject,
        body=body,
        sent_by=sent_by,
    )
    try:
        connection = get_connection()
        connection.open()
        message = DjangoEmailMessage(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[to_email],
            cc=cc,
            connection=connection,
        )
        message.send(fail_silently=False)
        connection.close()
        record.status = EMAIL_STATUS_SENT
        record.sent_at = timezone.now()
        record.save(update_fields=["status", "sent_at", "updated_at"])
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to send email %s to %s", template_slug, to_email)
        record.status = EMAIL_STATUS_FAILED
        record.error = str(exc)[:1000]
        record.save(update_fields=["status", "error", "updated_at"])
    return record
