"""Celery tasks for communications background work.

``send_bulk_email_chunk_task`` delivers one bounded chunk of a bulk email send
in the background (T5). The SMTP sending loop stays in the communications
service (``process_bulk_email_chunk``); this task only loads the record and
calls the service so the web request never blocks on large sends.
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger("apps.communications.tasks")


@shared_task(ignore_result=True)
def send_bulk_email_chunk_task(bulk_send_id: int, recipient_emails: list[str], total: int) -> None:
    """Send one bounded chunk of recipients for a bulk email send.

    Idempotent: ``process_bulk_email_chunk`` skips any recipient that already
    has a terminal EmailMessage record (sent/failed) for this bulk send, so
    Celery retries and manual re-runs never double-send. Per-recipient SMTP
    failures are recorded on the EmailMessage row by the service and never
    raise, so a chunk cannot abort mid-batch.
    """
    from apps.communications.services import process_bulk_email_chunk

    try:
        process_bulk_email_chunk(bulk_send_id, recipient_emails, total)
    except Exception as exc:  # noqa: BLE001 - unexpected chunk failure is logged; idempotency keeps a re-run safe
        logger.exception("Bulk email chunk failed for bulk_send=%s: %s", bulk_send_id, exc)
