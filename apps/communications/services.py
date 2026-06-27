"""Email sending service and template rendering helpers."""

from __future__ import annotations

import logging
import re
from typing import Any

from django.conf import settings
from django.core.mail import EmailMessage as DjangoEmailMessage
from django.core.mail import get_connection
from django.core.validators import validate_email
from django.db.models import Q
from django.template.exceptions import TemplateDoesNotExist
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.students.models import StudentProfile

from .constants import (
    BULK_STATUS_PARTIAL,
    BULK_STATUS_SENT,
    DESIGN_BRANDED,
    DESIGN_CHOICES,
    EMAIL_STATUS_FAILED,
    EMAIL_STATUS_SENT,
)
from .email_message import EmailMessage
from .models import BulkEmailSend, EmailFooter, EmailTemplate

logger = logging.getLogger("apps.communications")

VARIABLE_PATTERN = re.compile(r"{{\s*[a-zA-Z_][a-zA-Z0-9_]*\s*}}")


def _wrap_in_design(body: str, design: str, footer: str) -> str:
    """Wrap body in an email design template, returning full HTML."""
    if design not in dict(DESIGN_CHOICES):
        design = DESIGN_BRANDED
    return render_to_string(f"communications/email_designs/{design}.html", {
        "content": body, "footer": footer,
    })


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
) -> EmailMessage | None:
    """Render a template, wrap in design, send via SMTP, and record the result.

    Returns None silently if no active/enabled template exists for the slug.
    Raises ValueError if the rendered subject or body still contains
    unresolved {{ variable }} placeholders.
    """
    try:
        template = EmailTemplate.objects.get(slug=slug, is_active=True, is_enabled_for_action=True)
    except EmailTemplate.DoesNotExist:
        logger.warning("No active/enabled template found for slug=%s, skipping send", slug)
        return None
    subject, body = template.render(context)
    static_template = f"communications/email_designs/{slug}.html"
    try:
        body = render_to_string(static_template, {**context, "content": body})
    except TemplateDoesNotExist:
        pass
    if has_unresolved_variables(subject) or has_unresolved_variables(body):
        missing = VARIABLE_PATTERN.findall(subject) + VARIABLE_PATTERN.findall(body)
        raise ValueError(
            _("Template '%(slug)s' has unresolved variables after rendering: %(missing)s")
            % {"slug": slug, "missing": set(missing)}
        )
    footer = get_global_footer()
    html_body = _wrap_in_design(body, template.design, footer)
    return send_email(
        template_slug=slug,
        to_email=to_email,
        subject=subject,
        body=body,
        html_body=html_body,
        sent_by=sent_by,
        cc=cc or [],
    )


def send_email(
    *,
    template_slug: str,
    to_email: str,
    subject: str,
    body: str,
    html_body: str | None = None,
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
        payload = html_body or body
        message = DjangoEmailMessage(
            subject=subject,
            body=payload,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[to_email],
            cc=cc,
            connection=connection,
        )
        if html_body:
            message.content_subtype = "html"
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


RecipientEntry = dict[str, str]


def resolve_recipients(
    *,
    filter_criteria: dict | None = None,
    manual_emails: str = "",
    search_query: str = "",
) -> list[RecipientEntry]:
    """Resolve recipient email addresses from filter criteria, search, or manual list.

    Returns a list of dicts: {"email": str, "source": "filter"|"search"|"manual"}.
    Entries are deduplicated by normalized email across all sources.
    """
    entries: list[RecipientEntry] = []

    if filter_criteria:
        qs = StudentProfile.objects.select_related("user").all()
        diploma_id = filter_criteria.get("diploma_id")
        cohort_id = filter_criteria.get("cohort_id")
        status = filter_criteria.get("status")
        if diploma_id:
            qs = qs.filter(diploma_enrollments__cohort__diploma_id=diploma_id)
        if cohort_id:
            qs = qs.filter(diploma_enrollments__cohort_id=cohort_id)
        if status:
            qs = qs.filter(diploma_enrollments__status=status)
        for p in qs:
            if p.user.email:
                entries.append({"email": p.user.email, "source": "filter"})

    if search_query:
        q = search_query.strip()
        sq = StudentProfile.objects.select_related("user").filter(
            Q(full_name__icontains=q) | Q(user__email__icontains=q)
        )
        for p in sq:
            if p.user.email:
                entries.append({"email": p.user.email, "source": "search"})

    for raw in manual_emails.split("\n"):
        e = raw.strip()
        if not e:
            continue
        try:
            validate_email(e)
            entries.append({"email": e, "source": "manual"})
        except Exception:  # noqa: BLE001
            logger.warning("Skipping invalid email address: %s", e)

    seen: set[str] = set()
    result: list[RecipientEntry] = []
    for entry in entries:
        key = entry["email"].lower()
        if key not in seen:
            seen.add(key)
            result.append(entry)
    return result


def create_bulk_draft(
    *,
    subject: str,
    body: str,
    template_id: int | None = None,
    filter_criteria: dict | None = None,
    manual_emails: str = "",
    resolved_recipients: list[RecipientEntry] | None = None,
    sent_by,
) -> BulkEmailSend:
    recipients = resolved_recipients or resolve_recipients(
        filter_criteria=filter_criteria, manual_emails=manual_emails
    )
    template = None
    if template_id:
        template = EmailTemplate.objects.filter(id=template_id).first()
    return BulkEmailSend.objects.create(
        subject=subject,
        body=body,
        template=template,
        recipient_filter=filter_criteria or {},
        recipient_emails=manual_emails,
        resolved_recipients=recipients,
        recipient_count=len(recipients),
        sent_by=sent_by,
    )


def send_bulk_email(bulk_send: BulkEmailSend) -> BulkEmailSend:
    """Send all emails for a bulk send operation. Returns updated BulkEmailSend."""
    recipients = bulk_send.resolved_recipients if bulk_send.resolved_recipients else resolve_recipients(
        filter_criteria=bulk_send.recipient_filter if bulk_send.recipient_filter else None,
        manual_emails=bulk_send.recipient_emails,
    )
    if not recipients:
        bulk_send.status = BULK_STATUS_SENT
        bulk_send.sent_at = timezone.now()
        bulk_send.save(update_fields=["status", "sent_at", "updated_at"])
        return bulk_send
    design = bulk_send.template.design if bulk_send.template else DESIGN_BRANDED
    footer = get_global_footer()
    success = 0
    failure = 0
    for entry in recipients:
        html_body = _wrap_in_design(bulk_send.body, design, footer)
        record = send_email(
            template_slug=bulk_send.template.slug if bulk_send.template else "",
            to_email=entry["email"],
            subject=bulk_send.subject,
            body=bulk_send.body,
            html_body=html_body,
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
