"""Wait until the default database accepts a real query.

Docker's restart policy can start application containers in parallel after a
host restart, bypassing Compose's depends_on ordering. This command makes each
process wait for PostgreSQL to finish recovery before starting.
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.utils import InterfaceError, OperationalError


class Command(BaseCommand):
    help = "Block until the default database accepts a simple query."

    def add_arguments(self, parser):
        parser.add_argument(
            "--timeout",
            type=int,
            default=120,
            help="Maximum seconds to wait for the database (default: 120).",
        )

    def handle(self, *args, **options):
        timeout = max(1, options["timeout"])
        deadline = time.monotonic() + timeout
        connection = connections["default"]
        waiting_message_shown = False

        while True:
            try:
                # A real query catches PostgreSQL recovery states that still
                # accept TCP connections but reject normal database access.
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
            except (OperationalError, InterfaceError) as exc:
                connection.close()
                if time.monotonic() >= deadline:
                    raise CommandError(
                        f"Database did not become ready within {timeout} seconds: {exc}"
                    ) from exc
                if not waiting_message_shown:
                    self.stdout.write("Waiting for the database to become ready...")
                    waiting_message_shown = True
                time.sleep(1)
            else:
                self.stdout.write(self.style.SUCCESS("Database is ready."))
                return
