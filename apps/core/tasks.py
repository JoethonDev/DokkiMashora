"""Celery tasks for cross-app infrastructure maintenance.

``purge_ephemeral_data_task`` enforces the 30-day retention policy for
temporary/ephemeral data (REPOSITORY_AUDIT_IMPLEMENTATION_DECISIONS #33):
completed exam snapshots, email message bodies, and import raw payloads are
deleted or blanked after 30 days. Durable business history (payments, grades,
promotion decisions, audit events, certificate records, enrollment history) is
never touched. Every pass is bounded (small per-batch transactions) and
idempotent, so re-running the task is always safe.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger("apps.core.tasks")

#: Retention window for ephemeral data (DECISION #33).
RETENTION_DAYS = 30
#: Max rows mutated per atomic batch — keeps each transaction small under the
#: 2 vCPU / 4 GB infrastructure ceiling and avoids unbounded loads.
BATCH_SIZE = 500


def _iter_pk_batches(queryset, batch_size: int = BATCH_SIZE):
    """Yield bounded lists of primary keys from ``queryset``.

    Each iteration re-evaluates the queryset, so rows that no longer match the
    retention filter (already blanked or deleted) disappear from the next slice
    without ever loading more than ``batch_size`` rows at once.
    """
    while True:
        batch = list(queryset.values_list("pk", flat=True)[:batch_size])
        if not batch:
            return
        yield batch


def _blank_email_bodies(cutoff) -> int:
    """Blank the full HTML body of terminal EmailMessage rows older than cutoff.

    Only SENT/FAILED rows are touched — those are never delivered again
    (``deliver_queued_email`` skips terminal rows), so a blanked body can never
    produce an empty send. The delivery record (status, to_email, sent_at,
    template_slug, subject) is kept so audit/traceability stays intact.
    """
    from apps.communications.constants import EMAIL_STATUS_FAILED, EMAIL_STATUS_SENT
    from apps.communications.models import EmailMessage

    queryset = (
        EmailMessage.objects.filter(
            created_at__lt=cutoff,
            status__in=[EMAIL_STATUS_SENT, EMAIL_STATUS_FAILED],
            body__gt="",
        ).order_by("pk")
    )
    total = 0
    for batch in _iter_pk_batches(queryset):
        with transaction.atomic():
            total += EmailMessage.objects.filter(pk__in=batch).update(body="")
    return total


def _delete_terminal_snapshots(cutoff) -> int:
    """Delete answer snapshots of terminal entrance attempts older than cutoff.

    A snapshot is only meaningful while the attempt is in progress (auto-recovery
    and finalization copy it into ``EntranceAnswer`` at submit). Once the attempt
    is terminal (completed/graded) and its ``submitted_at`` is older than the
    retention window, the snapshot rows are safe to delete; the final answers,
    score, and result stay durable.
    """
    from apps.entrance_exams.constants import (
        ATTEMPT_STATUS_COMPLETED,
        ATTEMPT_STATUS_GRADED,
    )
    from apps.entrance_exams.models import EntranceSnapshot

    queryset = EntranceSnapshot.objects.filter(
        attempt__status__in=[ATTEMPT_STATUS_COMPLETED, ATTEMPT_STATUS_GRADED],
        attempt__submitted_at__lt=cutoff,
    ).order_by("pk")
    total = 0
    for batch in _iter_pk_batches(queryset):
        with transaction.atomic():
            total += EntranceSnapshot.objects.filter(pk__in=batch).delete()[0]
    return total


def _blank_import_raw_payloads(cutoff) -> dict[str, int]:
    """Blank the raw JSON payload of import rows older than cutoff.

    The import rows are kept (normalized match data, status, error messages, and
    the link to created/updated records stay observable) but their ``raw_data``
    copy of the uploaded file content is ephemeral and is removed after 30 days.
    """
    from apps.accounts.models import UserUploadRow
    from apps.payments.models import PaymentUploadRow

    totals = {"user_upload_rows_blanked": 0, "payment_upload_rows_blanked": 0}
    for model, key in (
        (UserUploadRow, "user_upload_rows_blanked"),
        (PaymentUploadRow, "payment_upload_rows_blanked"),
    ):
        queryset = model.objects.filter(created_at__lt=cutoff).exclude(raw_data={}).order_by("pk")
        for batch in _iter_pk_batches(queryset):
            with transaction.atomic():
                totals[key] += model.objects.filter(pk__in=batch).update(raw_data={})
    return totals


@shared_task(ignore_result=True)
def purge_ephemeral_data_task() -> dict:
    """Enforce the 30-day retention policy for ephemeral data (DECISION #33).

    Bounded and idempotent: each sub-cleanup mutates at most ``BATCH_SIZE`` rows
    per transaction and only matches rows that still need cleanup, so re-running
    the task is safe. Returns operational counts only — no student-sensitive
    data is logged or returned.
    """
    cutoff = timezone.now() - timedelta(days=RETENTION_DAYS)
    summary = {
        "retention_days": RETENTION_DAYS,
        "email_bodies_blanked": _blank_email_bodies(cutoff),
        "entrance_snapshots_deleted": _delete_terminal_snapshots(cutoff),
        **_blank_import_raw_payloads(cutoff),
    }
    logger.info("Ephemeral data retention cleanup: %s", summary)
    return summary