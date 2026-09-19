"""Opt-in request timing for controlled performance investigations."""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from contextlib import contextmanager

from django.conf import settings
from django.db import connection

logger = logging.getLogger("assessment.performance")
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")


class _QueryProbe:
    def __init__(self) -> None:
        self.count = 0
        self.total = 0.0
        self.slowest: list[tuple[float, str]] = []
        self.fingerprints: Counter[str] = Counter()

    def __call__(self, execute, sql, params, many, context):
        started = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            elapsed = time.perf_counter() - started
            self.count += 1
            self.total += elapsed
            fingerprint = _NUMBER.sub("?", " ".join(sql.split()))[:240]
            self.fingerprints[fingerprint] += 1
            self.slowest.append((elapsed, fingerprint))
            self.slowest = sorted(self.slowest, reverse=True)[:5]


@contextmanager
def stage(request, name: str):
    started = time.perf_counter()
    try:
        yield
    finally:
        metrics = getattr(request, "_assessment_perf", None)
        if metrics is not None:
            metrics["stages"][name] = metrics["stages"].get(name, 0.0) + (
                time.perf_counter() - started
            )


class PerformanceTimingMiddleware:
    """Capture request SQL and stage timings only when explicitly enabled."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, "ASSESSMENT_PERF_TRACE", False):
            return self.get_response(request)

        # Native Gunicorn queue estimate: nginx receive time via X-Request-Start.
        # nginx sets header to $msec (seconds.milliseconds since epoch).
        queue_ms = None
        nginx_start = request.headers.get("X-Request-Start")
        django_started_monotonic = time.perf_counter()
        django_started_wall = time.time() * 1000.0
        if nginx_start:
            try:
                nginx_ms = float(nginx_start) * 1000.0
                queue_ms = django_started_wall - nginx_ms
                # Clamp obvious clock skew / bad header.
                if not (-5000 < queue_ms < 120000):
                    queue_ms = None
            except Exception:
                queue_ms = None

        metrics = {"started": django_started_monotonic, "stages": {}, "queue_ms": queue_ms}
        request._assessment_perf = metrics
        probe = _QueryProbe()
        with connection.execute_wrapper(probe):
            response = self.get_response(request)
        metrics["total"] = time.perf_counter() - metrics["started"]
        metrics["sql_count"] = probe.count
        metrics["sql_total"] = probe.total
        metrics["response_bytes"] = len(response.content) if hasattr(response, "content") else 0

        if request.path.endswith(("/take/", "/start/", "/autosave/", "/submit/")):
            timing_parts = [f"django;dur={metrics['total'] * 1000:.1f}"]
            timing_parts += [f"{name};dur={duration * 1000:.1f}" for name, duration in metrics["stages"].items()]
            timing_parts.append(f"sql;dur={probe.total * 1000:.1f};desc=queries:{probe.count}")
            if queue_ms is not None:
                timing_parts.append(f"queue;dur={queue_ms:.1f}")
            response["Server-Timing"] = ", ".join(timing_parts)
            queue_hdr = f";queue_ms={queue_ms:.1f}" if queue_ms is not None else ""
            response["X-Assessment-Perf"] = (
                f"sql_count={probe.count};sql_ms={probe.total * 1000:.1f};"
                f"app_ms={metrics['total'] * 1000:.1f};bytes={metrics['response_bytes']}{queue_hdr}"
            )
            response["X-Queue-Ms"] = f"{queue_ms:.1f}" if queue_ms is not None else "unknown"
            logger.info(
                "assessment path=%s total_ms=%.1f queue_ms=%s sql_count=%d sql_ms=%.1f bytes=%d stages=%s slowest=%s duplicates=%s",
                request.path,
                metrics["total"] * 1000,
                f"{queue_ms:.1f}" if queue_ms is not None else "unknown",
                probe.count,
                probe.total * 1000,
                metrics["response_bytes"],
                {key: round(value * 1000, 1) for key, value in metrics["stages"].items()},
                [(round(value * 1000, 1), sql) for value, sql in probe.slowest],
                [item for item in probe.fingerprints.items() if item[1] > 1],
            )
        elif queue_ms is not None and request.path.endswith(("/start/", "/autosave/", "/submit/")):
            response["X-Queue-Ms"] = f"{queue_ms:.1f}"
        return response
