"""Email sending service and template rendering helpers."""

from __future__ import annotations

import contextvars
import logging
import re
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.mail import EmailMessage as DjangoEmailMessage
from django.core.mail import get_connection
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import F, Q
from django.template.exceptions import TemplateDoesNotExist
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from apps.applications.models import ProgramApplicationEmailLog
from apps.audit.constants import (
    ACTION_SYSTEM_EMAIL_FAILED,
    ACTION_SYSTEM_EMAIL_QUEUED,
    ACTION_SYSTEM_EMAIL_SENT,
    ACTION_SYSTEM_EMAIL_SKIPPED,
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    SECTION_COMMUNICATIONS,
)
from apps.audit.services import record_audit_event
from apps.communications import tasks as communication_tasks
from apps.students.models import StudentProfile

from .constants import (
    BULK_STATUS_PARTIAL,
    BULK_STATUS_SENT,
    DESIGN_BRANDED,
    DESIGN_CHOICES,
    EMAIL_STATUS_FAILED,
    EMAIL_STATUS_PROCESSING,
    EMAIL_STATUS_QUEUED,
    EMAIL_STATUS_SENT,
)
from .email_message import EmailMessage
from .models import BulkEmailSend, EmailFooter, EmailTemplate

logger = logging.getLogger("apps.communications")

VARIABLE_PATTERN = re.compile(r"{{\s*[a-zA-Z_][a-zA-Z0-9_]*\s*}}")


def _audit_email(
    action: str,
    record: EmailMessage,
    *,
    actor=None,
    outcome: str = OUTCOME_SUCCESS,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Record one email lifecycle event without ever blocking delivery."""
    payload = {
        "template_slug": record.template_slug,
        "recipient": record.to_email,
        "subject": record.subject,
        "attempts": record.attempts,
    }
    if metadata:
        payload.update(metadata)
    try:
        record_audit_event(
            actor,
            action,
            target_type="email_message",
            target_id=record.pk,
            section=SECTION_COMMUNICATIONS,
            target_label=f"{record.subject} → {record.to_email}"[:255],
            subject_type="email",
            subject_id=record.pk,
            subject_label=record.to_email,
            metadata=payload,
            outcome=outcome,
            correlation_id=f"email:{record.pk}",
        )
    except Exception:  # noqa: BLE001 - delivery must not fail because audit is down
        logger.exception("Could not record audit event %s for email %s", action, record.pk)


#: Templates that could not be used during the current request because no
#: active/enabled EmailTemplate row exists. The request-scoped collector lets
#: admin responses report "email not sent" instead of silently succeeding.
#: ``None`` means no collector is installed (management commands, Celery), so
#: recording is a deliberate no-op outside of a web request.
_UNAVAILABLE_TEMPLATES: contextvars.ContextVar[tuple[str, ...] | None] = contextvars.ContextVar(
    "unavailable_email_templates", default=None
)


def reset_unavailable_templates() -> None:
    """Start collecting unavailable templates for the current request."""
    _UNAVAILABLE_TEMPLATES.set(())


def consume_unavailable_templates() -> tuple[str, ...]:
    """Return the templates recorded so far and clear the collector."""
    slugs = _UNAVAILABLE_TEMPLATES.get() or ()
    _UNAVAILABLE_TEMPLATES.set(())
    return slugs


def _record_unavailable_template(slug: str) -> None:
    """Record that ``slug`` has no active/enabled template for this request."""
    slugs = _UNAVAILABLE_TEMPLATES.get()
    if slugs is None or slug in slugs:
        return
    _UNAVAILABLE_TEMPLATES.set((*slugs, slug))


def _mask_email(value: str) -> str:
    """Partially mask an address for logs (RV-025): keep the first 2 chars and
    the domain so operators can still triage, never the full local part.
    """
    if not value:
        return ""
    at = value.rfind("@")
    if at <= 0:
        return value[:2] + "***"
    local = value[:at]
    domain = value[at:]
    keep = local[:2] if len(local) > 2 else local
    return f"{keep}***{domain}"


def _wrap_in_design(body: str, design: str, footer: str) -> str:
    """Wrap body in an email design template, returning full HTML."""
    if design not in dict(DESIGN_CHOICES):
        design = DESIGN_BRANDED
    return render_to_string(
        f"communications/email_designs/{design}.html",
        {
            "content": body,
            "footer": footer,
        },
    )


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


def _render_templated_email(
    slug: str,
    context: dict[str, Any],
) -> tuple[EmailTemplate, str, str, str] | None:
    """Render an email template into (template, subject, body, html_body).

    Reused by the synchronous ``send_templated_email`` and the canonical
    ``queue_templated_email`` path so queueing and direct sending share the
    exact same rendering pipeline (static design wrap + global footer). Returns
    None when no active/enabled template exists. Raises ValueError when a
    template variable has no matching context key, so email content cannot
    silently lose values.
    """
    try:
        template = EmailTemplate.objects.get(slug=slug, is_active=True, is_enabled_for_action=True)
    except EmailTemplate.DoesNotExist:
        logger.warning("No active/enabled template found for slug=%s, skipping send", slug)
        _record_unavailable_template(slug)
        return None
    language = (get_language() or settings.LANGUAGE_CODE or "ar").split("-", 1)[0]
    context = {"language_code": language, **context}
    subject, body = template.render(context)
    missing = [v for v in template.variable_names() if v not in context]
    if "language_code" in template.body and "language_code" not in context:
        missing.append("language_code")
    if "{{ subject }}" in template.subject and "subject" not in context:
        missing.append("subject")
    if missing:
        raise ValueError(
            _("Template '%(slug)s' is missing context variables: %(missing)s")
            % {"slug": slug, "missing": ", ".join(sorted(missing))}
        )
    static_template = f"communications/email_designs/{slug}.html"
    try:
        body = render_to_string(static_template, {**context, "content": body})
    except TemplateDoesNotExist:
        pass
    footer = get_global_footer()
    html_body = _wrap_in_design(body, template.design, footer)
    return template, subject, body, html_body


def send_templated_email(
    slug: str,
    to_email: str,
    context: dict[str, Any],
    *,
    sent_by=None,
    cc: list[str] | None = None,
) -> EmailMessage | None:
    """Render a template, wrap in design, send via SMTP synchronously, and record the result.

    Returns None silently if no active/enabled template exists for the slug.
    Raises ValueError if a template variable has no matching context key.

    Kept synchronous so existing callers keep working; the canonical queueing
    path is ``queue_templated_email``.
    """
    rendered = _render_templated_email(slug, context)
    if rendered is None:
        return None
    _template, subject, body, html_body = rendered
    return send_email(
        template_slug=slug,
        to_email=to_email,
        subject=subject,
        body=body,
        html_body=html_body,
        sent_by=sent_by,
        cc=cc or [],
    )


def _send_over_smtp(
    *,
    to_email: str,
    subject: str,
    body: str,
    html_body: str | None,
    cc: list[str],
    connection=None,
) -> None:
    """Perform the actual SMTP delivery; raises on failure.

    Runs outside any DB transaction by design. Reused by the synchronous
    ``send_email`` path and the background queue task so the SMTP mechanics stay
    in one place. When a caller supplies an already-open ``connection`` (bulk
    chunks), it stays owned by that caller and is not closed here.
    """
    owns_connection = connection is None
    if owns_connection:
        connection = get_connection()
        connection.open()
    try:
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
    finally:
        if owns_connection:
            connection.close()


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
    connection=None,
) -> EmailMessage:
    """Send an email synchronously and record the result in EmailMessage."""
    cc = cc or []
    record = EmailMessage.objects.create(
        template_slug=template_slug,
        to_email=to_email,
        cc_emails=", ".join(cc),
        subject=subject,
        body=body,
        sent_by=sent_by,
        bulk_send=bulk_send,
        queued_at=timezone.now(),
    )
    try:
        _send_over_smtp(
            to_email=to_email,
            subject=subject,
            body=body,
            html_body=html_body,
            cc=cc,
            connection=connection,
        )
        record.status = EMAIL_STATUS_SENT
        record.sent_at = timezone.now()
        record.save(update_fields=["status", "sent_at", "updated_at"])
        _audit_email(ACTION_SYSTEM_EMAIL_SENT, record, actor=sent_by)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Failed to send email %s (message id %s) to %s",
            template_slug,
            record.pk,
            _mask_email(to_email),
        )
        record.status = EMAIL_STATUS_FAILED
        record.error = str(exc)[:1000]
        record.save(update_fields=["status", "error", "updated_at"])
        _audit_email(
            ACTION_SYSTEM_EMAIL_FAILED,
            record,
            actor=sent_by,
            outcome=OUTCOME_FAILURE,
            metadata={"error": record.error},
        )
    return record


def _dispatch_queued_email(delivery_id: int) -> None:
    """Hand one queued delivery to Celery without letting broker downtime escape.

    Runs from ``transaction.on_commit`` after the business transaction has
    already committed, so a broker outage must never surface as a 500 on an
    action that succeeded. The failure is logged and the row stays QUEUED, so a
    later retry or manual re-run can still deliver it.
    """
    try:
        communication_tasks.send_queued_email_task.delay(delivery_id)
    except Exception:  # noqa: BLE001 - broker outage must not fail the request
        logger.exception("Could not enqueue email delivery %s", delivery_id)


def queue_templated_email(
    slug: str,
    to_email: str,
    context: dict[str, Any],
    *,
    sent_by=None,
    cc: list[str] | None = None,
    reference: str | None = None,
    idempotency_key: str | None = None,
) -> EmailMessage | None:
    """Canonical queueing interface: persist a QUEUED delivery and enqueue it.

    Flow: business transaction -> commit -> ``transaction.on_commit`` -> Celery
    task -> worker sends the email. SMTP delivery therefore never determines
    whether the surrounding business transaction commits.

    Returns the EmailMessage row so callers can show queued feedback; returns
    None if no active/enabled template exists (same semantics as
    ``send_templated_email``). With ``idempotency_key`` the call is
    deduplicated: a second call with the same key returns the existing row
    instead of double-queueing. A terminal FAILED row with the same key is
    instead reset back to QUEUED and re-enqueued, so a re-run of a bulk action
    after permanent delivery failure actually re-sends rather than silently
    returning the stale row. The rendered subject and the final design-wrapped
    HTML body are stored so the worker delivers exactly what was rendered at
    queue time.
    """
    rendered = _render_templated_email(slug, context)
    if rendered is None:
        try:
            record_audit_event(
                sent_by,
                ACTION_SYSTEM_EMAIL_SKIPPED,
                target_type="email_template",
                target_id=slug,
                section=SECTION_COMMUNICATIONS,
                target_label=slug,
                subject_type="email",
                subject_label=to_email,
                metadata={
                    "template_slug": slug,
                    "recipient": to_email,
                    "reason": "template_missing_or_disabled",
                },
                outcome=OUTCOME_FAILURE,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not record skipped email audit for %s", slug)
        return None
    _template, subject, body, html_body = rendered

    defaults = {
        "template_slug": slug,
        "to_email": to_email,
        "cc_emails": ", ".join(cc or []),
        "subject": subject,
        "body": html_body,
        "status": EMAIL_STATUS_QUEUED,
        "sent_by": sent_by,
        "queued_at": timezone.now(),
        "reference": reference or "",
    }
    if idempotency_key:
        record, created = EmailMessage.objects.get_or_create(
            idempotency_key=idempotency_key,
            defaults=defaults,
        )
        if not created and record.status == EMAIL_STATUS_FAILED:
            # A previous attempt failed permanently: reset it so the delivery
            # task below re-sends instead of returning a stale terminal row.
            EmailMessage.objects.filter(pk=record.pk).update(
                status=EMAIL_STATUS_QUEUED,
                error="",
                queued_at=timezone.now(),
                updated_at=timezone.now(),
            )
            record.status = EMAIL_STATUS_QUEUED
            record.error = ""
            record.queued_at = timezone.now()
        elif not created:
            # Already queued/processing/sent for this key: deduplicate.
            return record
    else:
        record = EmailMessage.objects.create(**defaults)

    _audit_email(
        ACTION_SYSTEM_EMAIL_QUEUED,
        record,
        actor=sent_by,
        metadata={"reference": reference or ""},
    )

    # Reserve the delivery task for every row that still needs sending, whether
    # it was created above or reset from a terminal FAILED state. Scheduling is
    # deferred to commit so the worker can see the row. Previously the task was
    # only registered on the non-idempotent branch, so any idempotency-keyed
    # email (which is nearly all of them) stayed QUEUED forever: there is no
    # periodic sweeper that dispatches queued rows. ``robust=True`` keeps a
    # broker failure from raising out of an already-committed transaction.
    transaction.on_commit(lambda: _dispatch_queued_email(record.id), robust=True)
    return record


#: A PROCESSING row older than this lease is considered abandoned (crashed
#: worker) and may be reclaimed by a later delivery attempt.
EMAIL_DELIVERY_PROCESSING_LEASE_SECONDS = 600


def deliver_queued_email(delivery_id: int) -> bool:
    """Deliver one queued EmailMessage row over SMTP; raises on failure.

    Lifecycle transition QUEUED -> PROCESSING (claim) -> SENT. Returns False
    without sending when the row is already terminal (SENT/FAILED) or currently
    claimed by another worker, so retries and duplicate executions never
    double-send. Abandoned PROCESSING rows (started longer ago than the lease)
    are reclaimed so a crashed worker cannot strand an email. DB state
    transitions happen outside the SMTP call.
    """
    try:
        row = EmailMessage.objects.get(pk=delivery_id)
    except EmailMessage.DoesNotExist:
        logger.warning("EmailMessage %s no longer exists; delivery skipped", delivery_id)
        return False
    if row.status in (EMAIL_STATUS_SENT, EMAIL_STATUS_FAILED):
        return False
    now = timezone.now()
    stale_before = now - timedelta(seconds=EMAIL_DELIVERY_PROCESSING_LEASE_SECONDS)
    claimed = EmailMessage.objects.filter(
        Q(pk=row.pk)
        & (
            Q(status=EMAIL_STATUS_QUEUED)
            | (Q(status=EMAIL_STATUS_PROCESSING) & Q(started_at__lt=stale_before))
        )
    ).update(
        status=EMAIL_STATUS_PROCESSING,
        started_at=now,
        attempts=F("attempts") + 1,
        updated_at=now,
    )
    if not claimed:
        return False
    _send_over_smtp(
        to_email=row.to_email,
        subject=row.subject,
        body=row.body,
        html_body=row.body,
        cc=[c for c in (row.cc_emails or "").split(", ") if c],
    )
    EmailMessage.objects.filter(pk=row.pk).update(
        status=EMAIL_STATUS_SENT,
        sent_at=now,
        error="",
        updated_at=now,
    )
    _sync_application_log_from_delivery(row.pk, status=EMAIL_STATUS_SENT, error="")
    row.refresh_from_db(fields=["attempts", "status", "to_email", "subject", "template_slug"])
    _audit_email(ACTION_SYSTEM_EMAIL_SENT, row)
    return True


def record_email_delivery_failure(delivery_id: int, error: str, *, final: bool) -> None:
    """Record a delivery failure on an EmailMessage row.

    Non-final failures return the row to QUEUED so the next Celery attempt can
    claim it again; the last error is always persisted. Final failures become
    FAILED so they stay observable in the admin/UI.
    """
    if final:
        EmailMessage.objects.filter(pk=delivery_id).update(
            status=EMAIL_STATUS_FAILED,
            error=error[:1000],
            updated_at=timezone.now(),
        )
        _sync_application_log_from_delivery(delivery_id, status=EMAIL_STATUS_FAILED, error=error[:1000])
    else:
        EmailMessage.objects.filter(pk=delivery_id).update(
            status=EMAIL_STATUS_QUEUED,
            error=error[:1000],
            updated_at=timezone.now(),
        )
        _sync_application_log_from_delivery(delivery_id, status=EMAIL_STATUS_QUEUED, error=error[:1000])
    logger.warning(
        "email_delivery_failed email_id=%s final=%s error=%s",
        delivery_id,
        final,
        error[:300],
    )
    record = EmailMessage.objects.filter(pk=delivery_id).first()
    if record is not None:
        _audit_email(
            ACTION_SYSTEM_EMAIL_FAILED,
            record,
            outcome=OUTCOME_FAILURE,
            metadata={"error": error[:1000], "final": final},
        )


def _sync_application_log_from_delivery(delivery_id: int, *, status: str, error: str = "") -> None:
    """Keep the linked ProgramApplicationEmailLog in sync with EmailMessage status.

    The link is via ``ProgramApplicationEmailLog.email_message_id`` set at
    queue time. ``failure -> queued`` re-shows the log as queued so a retry
    remains visible as pending.
    """
    now = timezone.now()
    update_fields: dict[str, object] = {"status": status, "error": error, "updated_at": now}
    if status == EMAIL_STATUS_SENT:
        update_fields["sent_at"] = now
    try:
        ProgramApplicationEmailLog.objects.filter(email_message_id=delivery_id).update(**update_fields)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to sync application email log for delivery %s", delivery_id)


def record_program_application_email_log(
    *,
    program_application,
    action: str,
    template_slug: str,
    recipient: str,
    sent_by=None,
    status: str = EMAIL_STATUS_QUEUED,
    error: str = "",
    is_resend: bool | None = None,
    idempotency_key: str | None = None,
    email_message=None,
    sent_at=None,
):
    """Single canonical write path for ``ProgramApplicationEmailLog``.

    Handles ``recipient``, ``action``, ``template``, ``status``, ``resend``,
    ``error``, ``timestamps``, ``actor`` (``sent_by``) and ``idempotency``.
    ``is_resend`` is derived from existing non-resend logs when not given.
    When ``idempotency_key`` is given, a matching log is returned instead of
    creating a duplicate (bulk-invitation idempotency).
    """
    recipient = (recipient or "").strip()
    if is_resend is None:
        is_resend = ProgramApplicationEmailLog.objects.filter(
            program_application=program_application,
            action=action,
            status__in=[EMAIL_STATUS_SENT, EMAIL_STATUS_QUEUED, EMAIL_STATUS_PROCESSING],
            is_resend=False,
        ).exists()
    if idempotency_key:
        existing = ProgramApplicationEmailLog.objects.filter(idempotency_key=idempotency_key).first()
        if existing is not None:
            return existing
    now = sent_at or timezone.now()
    # ``sent_at`` mirrors EmailMessage lifecycle: queued/processing -> queued_at,
    # sent -> sent_at, failed -> now with error.
    if status in (EMAIL_STATUS_QUEUED, EMAIL_STATUS_PROCESSING):
        sent_at_value = now
    elif status == EMAIL_STATUS_SENT:
        sent_at_value = now
    elif status == EMAIL_STATUS_FAILED:
        sent_at_value = now if error else None
    else:
        sent_at_value = now
    return ProgramApplicationEmailLog.objects.create(
        program_application=program_application,
        action=action,
        template_slug=template_slug,
        recipient=recipient,
        sent_by=sent_by,
        status=status,
        error=error[:1000] if error else "",
        is_resend=bool(is_resend),
        idempotency_key=idempotency_key,
        email_message=email_message,
        sent_at=sent_at_value,
    )


def queue_program_application_email(
    *,
    program_application,
    action: str,
    template_slug: str,
    to_email: str,
    context: dict[str, Any],
    sent_by=None,
    idempotency_key: str | None = None,
    reference: str | None = None,
    is_resend: bool | None = None,
    cc: list[str] | None = None,
) -> tuple[EmailMessage | None, object | None]:
    """Business action -> central email queue -> log.

    Queues the templated email via ``queue_templated_email`` (Celery) and
    records the application email log via the single ``record_program_application_email_log``
    path. Returns ``(EmailMessage, ProgramApplicationEmailLog)``; ``(None, None)``
    when no active template exists. Respects ``idempotency_key`` for dedup and
    correctly sets ``is_resend``.

    This is the canonical entry point for all application-bound emails
    (including BUG-005 Option A resends).
    """
    to_email = (to_email or "").strip()
    if not to_email:
        return None, None
    if is_resend is None:
        is_resend = ProgramApplicationEmailLog.objects.filter(
            program_application=program_application,
            action=action,
            status__in=[EMAIL_STATUS_SENT, EMAIL_STATUS_QUEUED, EMAIL_STATUS_PROCESSING],
            is_resend=False,
        ).exists()
    if reference is None:
        reference = f"app:{program_application.pk}"
    record = queue_templated_email(
        template_slug,
        to_email,
        context,
        sent_by=sent_by,
        cc=cc,
        reference=reference,
        idempotency_key=idempotency_key,
    )
    if record is None:
        # No active template: log a failed attempt so the audit trail is not silent.
        log = record_program_application_email_log(
            program_application=program_application,
            action=action,
            template_slug=template_slug,
            recipient=to_email,
            sent_by=sent_by,
            status=EMAIL_STATUS_FAILED,
            error="No active template",
            is_resend=is_resend,
            idempotency_key=idempotency_key,
            email_message=None,
        )
        return None, log
    # If EmailMessage was deduplicated (idempotency hit), reuse the existing log
    # for that key instead of creating a duplicate application log.
    if idempotency_key:
        existing_log = ProgramApplicationEmailLog.objects.filter(idempotency_key=idempotency_key).first()
        if existing_log is not None:
            # Ensure the existing log points at the deduped EmailMessage
            if existing_log.email_message_id != record.pk:
                ProgramApplicationEmailLog.objects.filter(pk=existing_log.pk).update(email_message=record)
            return record, existing_log
    log = record_program_application_email_log(
        program_application=program_application,
        action=action,
        template_slug=template_slug,
        recipient=to_email,
        sent_by=sent_by,
        status=record.status or EMAIL_STATUS_QUEUED,
        error=record.error or "",
        is_resend=is_resend,
        idempotency_key=idempotency_key or record.idempotency_key,
        email_message=record,
        sent_at=record.queued_at or timezone.now(),
    )
    return record, log


# Backwards-compatible alias for the spec wording ``queue_email``.
def queue_email(*args, **kwargs):  # noqa: ANN002, ANN201
    """Alias to :func:`queue_program_application_email` for spec compliance."""
    return queue_program_application_email(*args, **kwargs)


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
            logger.warning("Skipping invalid email address: %s", _mask_email(e))

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


_BULK_EMAIL_CHUNK_SIZE = 100


def enqueue_bulk_email_send(bulk_send: BulkEmailSend) -> int:
    """Enqueue a bulk email send as bounded, idempotent chunk tasks.

    Returns the number of chunk tasks enqueued. The web request never blocks on
    SMTP delivery (T5); each chunk task sends at most ``_BULK_EMAIL_CHUNK_SIZE``
    recipients and skips recipients that already have a terminal send record.
    """
    recipients = (
        bulk_send.resolved_recipients
        if bulk_send.resolved_recipients
        else resolve_recipients(
            filter_criteria=bulk_send.recipient_filter if bulk_send.recipient_filter else None,
            manual_emails=bulk_send.recipient_emails,
        )
    )
    total = len(recipients)
    if total == 0:
        bulk_send.status = BULK_STATUS_SENT
        bulk_send.sent_at = timezone.now()
        bulk_send.save(update_fields=["status", "sent_at", "updated_at"])
        return 0
    chunk_count = 0
    for offset in range(0, total, _BULK_EMAIL_CHUNK_SIZE):
        chunk_emails = [entry["email"] for entry in recipients[offset : offset + _BULK_EMAIL_CHUNK_SIZE]]
        communication_tasks.send_bulk_email_chunk_task.delay(bulk_send.id, chunk_emails, total)
        chunk_count += 1
    return chunk_count


def process_bulk_email_chunk(bulk_send_id: int, recipient_emails: list[str], total: int) -> tuple[int, int]:
    """Send one bounded chunk of recipients for a bulk send. Returns (success, failure).

    Idempotent per recipient: an email that already has a terminal EmailMessage
    record (sent/failed) for this bulk send is skipped, so a task retry or
    manual re-run never double-sends. Per-recipient SMTP failures are recorded
    on the EmailMessage row by ``send_email`` and do not abort the remaining
    recipients in the chunk.
    """
    try:
        bulk_send = (
            BulkEmailSend.objects.select_related("template").defer("resolved_recipients").get(id=bulk_send_id)
        )
    except BulkEmailSend.DoesNotExist:
        logger.warning("Bulk send %s no longer exists; chunk skipped", bulk_send_id)
        return 0, 0
    if not recipient_emails:
        return 0, 0
    already_processed = set(
        EmailMessage.objects.filter(
            bulk_send=bulk_send,
            to_email__in=recipient_emails,
            status__in=[EMAIL_STATUS_SENT, EMAIL_STATUS_FAILED],
        ).values_list("to_email", flat=True)
    )
    design = bulk_send.template.design if bulk_send.template else DESIGN_BRANDED
    footer = get_global_footer()
    html_body = _wrap_in_design(bulk_send.body, design, footer)
    success = 0
    failure = 0
    connection = get_connection()
    try:
        connection.open()
    except Exception:  # noqa: BLE001 - fall back to a per-message connection
        logger.exception("Could not open the bulk SMTP connection; using per-message connections")
        connection = None
    try:
        for email in recipient_emails:
            if email in already_processed:
                continue
            record = send_email(
                template_slug=bulk_send.template.slug if bulk_send.template else "",
                to_email=email,
                subject=bulk_send.subject,
                body=bulk_send.body,
                html_body=html_body,
                sent_by=bulk_send.sent_by,
                bulk_send=bulk_send,
                connection=connection,
            )
            if record.status == EMAIL_STATUS_SENT:
                success += 1
            else:
                failure += 1
    finally:
        if connection is not None:
            connection.close()
    _accumulate_bulk_send_progress(bulk_send_id, success, failure, total)
    return success, failure


def _accumulate_bulk_send_progress(bulk_send_id: int, success: int, failure: int, total: int) -> None:
    """Accumulate chunk results into the bulk send using atomic F() counters.

    Counters are monotonic, so concurrent chunk tasks cannot overwrite a
    failure with a later success: ``partial`` is set once any failure exists and
    ``sent`` only once every recipient has been processed with zero failures.
    """
    BulkEmailSend.objects.filter(id=bulk_send_id).update(
        success_count=F("success_count") + success,
        failure_count=F("failure_count") + failure,
        updated_at=timezone.now(),
    )
    row = (
        BulkEmailSend.objects.filter(id=bulk_send_id)
        .values(
            "success_count",
            "failure_count",
        )
        .first()
    )
    if row is None:
        return
    processed = row["success_count"] + row["failure_count"]
    if row["failure_count"] > 0:
        new_status = BULK_STATUS_PARTIAL
    elif processed >= total:
        new_status = BULK_STATUS_SENT
    else:
        return
    BulkEmailSend.objects.filter(id=bulk_send_id).update(
        status=new_status,
        sent_at=timezone.now(),
        updated_at=timezone.now(),
    )
