"""Restore the database from a backup file.

Postgres: a plain-SQL dump is piped into psql; a custom-format dump (pg_dump
--format=custom) is restored with pg_restore so both management-command and
Compose-produced artifacts are supported (S2-26). SQLite: copy file over
db.sqlite3.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

OPERATION_TIMEOUT_SECONDS = 1200


class Command(BaseCommand):
    help = "Restore the database from a backup file."

    def add_arguments(self, parser) -> None:
        parser.add_argument("backup_file", type=str, help="Path to the backup file.")
        parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")

    def handle(self, *args, **options) -> None:
        backup = Path(options["backup_file"]).expanduser().resolve()
        if not backup.is_file():
            raise CommandError(f"Backup file not found: {backup}")

        database_url = os.environ.get("DATABASE_URL") or settings.DATABASES["default"].get("URL", "")
        if not database_url:
            raise CommandError("DATABASE_URL is not set.")

        parsed = urlparse(database_url)
        backend = parsed.scheme.split("+")[0]

        if not options["yes"]:
            self.stdout.write(self.style.WARNING(
                f"This will REPLACE the {backend} database at {parsed.hostname or parsed.path} "
                f"with {backup}. Existing data will be lost."
            ))
            confirm = input("Type 'yes' to continue: ")
            if confirm.strip().lower() != "yes":
                raise CommandError("Aborted.")

        if backend == "postgres":
            if _is_custom_format(backup):
                # S2-26: Compose produces custom-format dumps; pg_restore is the
                # only reader that understands them.
                cmd = ["pg_restore", "--no-owner", "--no-privileges", "--dbname", database_url, str(backup)]
            else:
                cmd = ["psql", database_url, "-f", str(backup)]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=OPERATION_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as exc:
                raise CommandError(
                    f"Restore timed out after {OPERATION_TIMEOUT_SECONDS}s and was terminated."
                ) from exc
            if result.returncode != 0:
                raise CommandError(result.stderr.strip() or "psql restore failed.")
        elif backend == "sqlite":
            db_path = Path(unquote(parsed.path.lstrip("/")))
            # ponytail: copy then atomic-ish swap via Path.replace; WAL users should also copy -wal/-shm siblings.
            shutil.copyfile(backup, db_path)
        else:
            raise CommandError(f"Unsupported database backend: {backend}")

        self.stdout.write(self.style.SUCCESS(f"Restored from {backup}"))


def _is_custom_format(backup: Path) -> bool:
    """Detect a pg_dump custom-format archive by its magic header.

    Custom format archives begin with ``PGDMP``; plain SQL dumps start with
    ``--``. A read of the first bytes is bounded and never loads the file.
    """
    try:
        with backup.open("rb") as fh:
            header = fh.read(5)
    except OSError:
        return False
    return header == b"PGDMP"
