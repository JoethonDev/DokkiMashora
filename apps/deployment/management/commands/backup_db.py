"""Dump the database to backups/<timestamp>.{sql,sqlite3}.

Supports Postgres (pg_dump) and SQLite (sqlite3 .backup).
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

BACKUPS_DIR = Path(settings.BASE_DIR) / "backups"
PROBE_NAME = "__deployment_backup_probe__"


class Command(BaseCommand):
    help = "Back up the database to backups/."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--keep", type=int, default=0, help="Keep only the N most recent backups.")

    def handle(self, *args, **options) -> None:
        database_url = os.environ.get("DATABASE_URL") or settings.DATABASES["default"].get("URL", "")
        if not database_url:
            raise CommandError("DATABASE_URL is not set.")
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

        parsed = urlparse(database_url)
        backend = parsed.scheme.split("+")[0]
        ts = time.strftime("%Y%m%d-%H%M%S")

        if backend == "postgres":
            out_file = BACKUPS_DIR / f"{ts}.sql"
            cmd = [
                "pg_dump",
                "--no-owner",
                "--no-privileges",
                database_url,
                "-f",
                str(out_file),
            ]
        elif backend == "sqlite":
            db_path = unquote(parsed.path.lstrip("/"))
            out_file = BACKUPS_DIR / f"{ts}.sqlite3"
            # ponytail: sqlite3 .backup is the safe online backup; shutil.copy would risk torn writes.
            cmd = ["sqlite3", db_path, f".backup {out_file}"]
        else:
            raise CommandError(f"Unsupported database backend: {backend}")

        self.stdout.write(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise CommandError(result.stderr.strip() or "Backup command failed.")
        self.stdout.write(self.style.SUCCESS(f"Backup written to {out_file}"))

        keep = int(options.get("keep") or 0)
        if keep > 0:
            self._prune(backend, keep)

    def _prune(self, backend: str, keep: int) -> None:
        suffix = ".sql" if backend == "postgres" else ".sqlite3"
        files = sorted(BACKUPS_DIR.glob(f"*{suffix}"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[keep:]:
            old.unlink()
            self.stdout.write(f"Pruned {old.name}")
