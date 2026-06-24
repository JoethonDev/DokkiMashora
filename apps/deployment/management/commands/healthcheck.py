"""Run health_check() and emit JSON. Exit 0 if healthy, 1 otherwise."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from apps.deployment.health import health_check


class Command(BaseCommand):
    help = "Probe database, cache, and storage; emit JSON status."

    def handle(self, *args, **options) -> None:
        report = health_check()
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
        raise SystemExit(0 if report["status"] == "ok" else 1)
