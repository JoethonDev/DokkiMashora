"""Email sending service and template rendering helpers."""

from __future__ import annotations

import logging
import re
from typing import Any

from django.conf import settings
from django.core.mail import EmailMessage as DjangoEmailMessage
from django.core.mail import get_connection
from django.core.validators import validate_email
from django.utils import timezone

from .constants import (
    BULK_STATUS_PARTIAL,
    BULK_STATUS_SENT,
    EMAIL_STATUS_FAILED,
    EMAIL_STATUS_SENT,
)
from .email_message import EmailMessage
from .models import BulkEmailSend, EmailFooter, EmailTemplate

logger = logging.getLogger("apps.communications")

VARIABLE_PATTERN = re.compile(r"{{\s*[a-zA-Z_][a-zA-Z0-9_]*\s*}}")


def has_unresolved_variables(text: str) -> bool:
    """Return True if the text contains unresolved {{ variable }} placeholders."""
    return bool(VARIABLE_PATTERN.search(text))


def get_global_footer() -> str:
    """Return the global email footer body, or empty string."""
    footer = EmailFooter.objects.first()
    return footer.body if footer else ""


def render_template(slug: str, context: dict[str, Any]) -> tuple[str, str]:
    """Render an EmailTemplate by slug, returning (subject, body)."""
    template = EmailTemplate.objects.get(slug=slug, is_active=True, is_enabled_for_action=True)
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
    template = EmailTemplate.objects.get(slug=slug, is_active=True, is_enabled_for_action=True)
    subject, body = template.render(context)
    footer = get_global_footer()
    if footer:
        body = template.render(context)[1] + "\n" + footer
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
    bulk_send: BulkEmailSend | None = None,
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
        bulk_send=bulk_send,
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


def resolve_recipients(*, filter_criteria: dict | None = None, manual_emails: str = "") -> list[str]:
    """Resolve a list of recipient email addresses from filter criteria or manual list.

    Manual emails are deduplicated and validated. Invalid addresses are silently skipped.
    Filter-based and manual lists are merged and deduplicated.
    """
    from apps.students.models import StudentProfile

    emails: list[str] = []

    if filter_criteria:
        qs = StudentProfile.objects.select_related("user").all()
        diploma_id = filter_criteria.get("diploma_id")
        cohort_id = filter_criteria.get("cohort_id")
        status = filter_criteria.get("status")
        if diploma_id:
            qs = qs.filter(diplomaenrollment__cohort__diploma_id=diploma_id)
        if cohort_id:
            qs = qs.filter(diplomaenrollment__cohort_id=cohort_id)
        if status:
            qs = qs.filter(diplomaenrollment__status=status)
        emails = [p.user.email for p in qs if p.user.email]

    for raw in manual_emails.split("\n"):
        e = raw.strip()
        if not e:
            continue
        try:
            validate_email(e)
            emails.append(e)
        except Exception:  # noqa: BLE001
            logger.warning("Skipping invalid email address: %s", e)

    seen: set[str] = set()
    result: list[str] = []
    for e in emails:
        key = e.lower()
        if key not in seen:
            seen.add(key)
            result.append(e)
    return result


def create_bulk_draft(
    *,
    subject: str,
    body: str,
    template_id: int | None = None,
    filter_criteria: dict | None = None,
    manual_emails: str = "",
    sent_by,
) -> BulkEmailSend:
    recipients = resolve_recipients(filter_criteria=filter_criteria, manual_emails=manual_emails)
    template = None
    if template_id:
        template = EmailTemplate.objects.filter(id=template_id).first()
    return BulkEmailSend.objects.create(
        subject=subject,
        body=body,
        template=template,
        recipient_filter=filter_criteria or {},
        recipient_emails=manual_emails,
        recipient_count=len(recipients),
        sent_by=sent_by,
    )


def send_bulk_email(bulk_send: BulkEmailSend) -> BulkEmailSend:
    """Send all emails for a bulk send operation. Returns updated BulkEmailSend."""
    recipients = resolve_recipients(
        filter_criteria=bulk_send.recipient_filter if bulk_send.recipient_filter else None,
        manual_emails=bulk_send.recipient_emails,
    )
    if not recipients:
        bulk_send.status = BULK_STATUS_SENT
        bulk_send.sent_at = timezone.now()
        bulk_send.save(update_fields=["status", "sent_at", "updated_at"])
        return bulk_send
    success = 0
    failure = 0
    for email in recipients:
        record = send_email(
            template_slug=bulk_send.template.slug if bulk_send.template else "",
            to_email=email,
            subject=bulk_send.subject,
            body=bulk_send.body,
            sent_by=bulk_send.sent_by,
            bulk_send=bulk_send,
        )
        if record.status == EMAIL_STATUS_SENT:
            success += 1
        else:
            failure += 1
    bulk_send.success_count = success
    bulk_send.failure_count = failure
    bulk_send.status = BULK_STATUS_SENT if failure == 0 else BULK_STATUS_PARTIAL
    bulk_send.sent_at = timezone.now()
    bulk_send.save(update_fields=[
        "success_count", "failure_count", "status", "sent_at", "updated_at",
    ])
    return bulk_send
