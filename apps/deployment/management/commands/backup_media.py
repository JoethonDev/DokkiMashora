"""Archive the local media root (MEDIA_ROOT) to backups/media-<timestamp>.tar.gz.

Media is a separate durable source from the database (S2-10); a DB-only dump
cannot restore uploaded documents, lecture audio, or generated media. This
command snapshots the local MEDIA_ROOT used by the Compose media bind mount.
It is bounded by an operational timeout and reports a FAILED backup rather
than hanging. Credentials and private URLs never live under MEDIA_ROOT and are
not part of the archive.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

BACKUPS_DIR = Path(settings.BASE_DIR) / "backups"
OPERATION_TIMEOUT_SECONDS = 1200


class Command(BaseCommand):
    help = "Archive the local media root to backups/media-<timestamp>.tar.gz."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--keep", type=int, default=0, help="Keep only the N most recent media backups.")

    def handle(self, *args, **options) -> None:
        media_root = Path(settings.MEDIA_ROOT)
        if not media_root.is_dir():
            raise CommandError(f"MEDIA_ROOT does not exist or is not a directory: {media_root}")
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

        ts = time.strftime("%Y%m%d-%H%M%S")
        out_file = BACKUPS_DIR / f"media-{ts}.tar.gz"
        # tar streams the tree; it never loads the whole media root into memory.
        cmd = ["tar", "-czf", str(out_file), "-C", str(media_root), "."]

        self.stdout.write(f"Running: {' '.join(cmd)}")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=OPERATION_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            raise CommandError(
                f"Media backup timed out after {OPERATION_TIMEOUT_SECONDS}s and was terminated."
            ) from exc
        if result.returncode != 0:
            raise CommandError(result.stderr.strip() or "Media backup command failed.")
        self.stdout.write(self.style.SUCCESS(f"Media backup written to {out_file}"))

        keep = int(options.get("keep") or 0)
        if keep > 0:
            self._prune(keep)

    def _prune(self, keep: int) -> None:
        files = sorted(BACKUPS_DIR.glob("media-*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[keep:]:
            old.unlink()
            self.stdout.write(f"Pruned {old.name}")
