"""Retry deferred storage deletions (S2-33).

Reconciles physical-object cleanup that failed after commit: each pending
``DeferredStorageDeletion`` row is retried against the storage backend and
cleared on success. Runs in bounded batches and is idempotent, so it can be
invoked repeatedly (e.g. by a daily cron/systemd timer alongside the backup
profile) without side effects.
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from apps.storage.models import DeferredStorageDeletion
from apps.storage.services import storage_service
from django.db import transaction

logger = logging.getLogger("apps.storage")


class Command(BaseCommand):
    help = "Retry failed post-commit storage deletions recorded by cleanup."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--limit", type=int, default=100,
            help="Maximum number of deferred deletions to retry per run.",
        )

    def handle(self, *args, **options) -> None:
        limit = max(1, int(options.get("limit") or 100))
        pending = list(DeferredStorageDeletion.objects.order_by("created_at")[:limit])
        if not pending:
            self.stdout.write("No deferred storage deletions to reconcile.")
            return

        processed = cleared = failed = 0
        for item in pending:
            # Remote object-store I/O must never hold a DB transaction open
            # (RV-015 / AGENTS.md). Perform the deletes first, then update the
            # durable claim in a short locked transaction.
            ok = True
            if item.key:
                ok = storage_service.delete_file(item.key) and ok
            if item.prefix:
                ok = storage_service.delete_prefix(item.prefix) and ok
            with transaction.atomic():
                locked = DeferredStorageDeletion.objects.select_for_update().get(pk=item.pk)
                if ok:
                    locked.delete()
                    cleared += 1
                else:
                    locked.attempts += 1
                    locked.last_error = "reconciliation retry failed"
                    locked.save(update_fields=["attempts", "last_error", "updated_at"])
                    failed += 1
                processed += 1

        self.stdout.write(
            f"Reconciled {processed} deferred deletion(s): {cleared} cleared, {failed} still failing."
        )
        if failed:
            logger.warning("Storage reconciliation left %s deletion(s) pending.", failed)
