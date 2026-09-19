"""Celery tasks for communications background work.

``send_queued_email_task`` is the canonical worker for the queued-email
delivery lifecycle: QUEUED -> PROCESSING -> SENT, or FAILED after bounded
retries. ``send_bulk_email_chunk_task`` delivers one bounded chunk of a bulk
email send in the background. The SMTP loops stay in the communications service;
these tasks only load records and call the service so web requests never block
on email delivery.
"""

from __future__ import annotations

import logging

from celery import shared_task

from apps.communications import services as communication_services

logger = logging.getLogger("apps.communications.tasks")

#: Bounded retry policy for transient SMTP failures. Each retry re-claims the
#: delivery row; the row only becomes FAILED after the last attempt.
EMAIL_TASK_MAX_RETRIES = 3
EMAIL_TASK_RETRY_BACKOFF_SECONDS = 15


@shared_task(bind=True, ignore_result=True, max_retries=EMAIL_TASK_MAX_RETRIES)
def send_queued_email_task(self, delivery_id: int) -> None:
    """Deliver one queued EmailMessage row (delivery_id) over SMTP.

    Lifecycle: QUEUED -> PROCESSING -> SENT on success; on a transient failure
    the row is returned to QUEUED and retried with bounded exponential backoff,
    and becomes FAILED after the last attempt so it stays observable. The task
    argument is only the delivery id, so no unbounded payloads cross the broker.
    Idempotent: terminal rows are never re-sent and the exclusive QUEUED claim
    in ``deliver_queued_email`` prevents concurrent duplicate sends.
    """
    try:
        delivered = communication_services.deliver_queued_email(delivery_id)
    except Exception as exc:  # noqa: BLE001 - SMTP failure is retried then surfaced as FAILED
        logger.exception(
            "Email delivery %s attempt %s failed: %s",
            delivery_id, self.request.retries + 1, exc,
        )
        if self.request.retries < self.max_retries:
            communication_services.record_email_delivery_failure(delivery_id, str(exc), final=False)
            raise self.retry(
                exc=exc,
                countdown=EMAIL_TASK_RETRY_BACKOFF_SECONDS * (2 ** self.request.retries),
            ) from exc
        communication_services.record_email_delivery_failure(delivery_id, str(exc), final=True)
        logger.error(
            "Email delivery %s failed permanently after %s attempts",
            delivery_id, self.max_retries + 1,
        )
        return
    if delivered:
        logger.info("Email delivery %s sent", delivery_id)


@shared_task(ignore_result=True)
def send_bulk_email_chunk_task(bulk_send_id: int, recipient_emails: list[str], total: int) -> None:
    """Send one bounded chunk of recipients for a bulk email send.

    Idempotent: ``process_bulk_email_chunk`` skips any recipient that already
    has a terminal EmailMessage record (sent/failed) for this bulk send, so
    Celery retries and manual re-runs never double-send. Per-recipient SMTP
    failures are recorded on the EmailMessage row by the service and never
    raise, so a chunk cannot abort mid-batch.
    """
    try:
        communication_services.process_bulk_email_chunk(bulk_send_id, recipient_emails, total)
    except Exception as exc:  # noqa: BLE001 - unexpected chunk failure is logged; idempotency keeps a re-run safe
        logger.exception("Bulk email chunk failed for bulk_send=%s: %s", bulk_send_id, exc)
