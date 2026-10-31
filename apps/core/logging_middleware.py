"""Request logging with searchable request IDs and outcome details."""

from __future__ import annotations

import logging
import time

from django.conf import settings
from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.http import Http404
from django.utils.deprecation import MiddlewareMixin

from .logging import (
    clean_ip,
    clear_request_context,
    normalize_request_id,
    redact_request_path,
    set_request_context,
    set_request_result,
)

logger = logging.getLogger("apps.request")
_EXCLUDED_PREFIXES = ("/static/", "/media/", "/healthz", "/deployment/health/")


class RequestLoggingMiddleware(MiddlewareMixin):
    """Attach request IDs and emit warning/error lines for Dozzle."""

    def process_request(self, request):
        request_id = normalize_request_id(request.headers.get("X-Request-ID"))
        request._log_request_id = request_id
        request._log_started_at = time.perf_counter()
        user = getattr(request, "user", None)
        authenticated = bool(getattr(user, "is_authenticated", False))
        user_label = (
            str(getattr(user, "email", "") or getattr(user, "pk", "")) if authenticated else "anonymous"
        )
        ip_address = clean_ip(
            request.META.get("HTTP_X_REAL_IP")
            or (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
            or request.META.get("REMOTE_ADDR")
        )
        set_request_context(
            request_id=request_id,
            method=request.method,
            path=request.path,
            user_label=user_label,
            ip_address=ip_address,
        )
        return None

    def process_response(self, request, response):
        started = getattr(request, "_log_started_at", None)
        duration_ms = int((time.perf_counter() - started) * 1000) if started else 0
        status_code = int(getattr(response, "status_code", 0) or 0)
        set_request_result(status_code=status_code, duration_ms=duration_ms)
        if getattr(request, "_log_exception_logged", False):
            clear_request_context()
            return response
        path = request.path or ""
        if not any(path.startswith(prefix) for prefix in _EXCLUDED_PREFIXES):
            if status_code >= 500:
                logger.error(
                    "request_server_error method=%s path=%s status=%s duration_ms=%s",
                    request.method,
                    redact_request_path(path),
                    status_code,
                    duration_ms,
                )
            elif status_code >= 400:
                logger.warning(
                    "request_client_error method=%s path=%s status=%s duration_ms=%s",
                    request.method,
                    redact_request_path(path),
                    status_code,
                    duration_ms,
                )
            elif duration_ms >= int(getattr(settings, "SLOW_REQUEST_LOG_MS", 2000)):
                logger.warning(
                    "request_slow method=%s path=%s status=%s duration_ms=%s",
                    request.method,
                    redact_request_path(path),
                    status_code,
                    duration_ms,
                )
        clear_request_context()
        return response

    def process_exception(self, request, exception):
        request._log_exception_logged = True
        started = getattr(request, "_log_started_at", None)
        duration_ms = int((time.perf_counter() - started) * 1000) if started else 0
        if isinstance(exception, Http404):
            status_code = 404
        elif isinstance(exception, PermissionDenied):
            status_code = 403
        elif isinstance(exception, SuspiciousOperation):
            status_code = 400
        else:
            status_code = 500
        set_request_result(status_code=status_code, duration_ms=duration_ms)
        path = redact_request_path(request.path)
        if isinstance(exception, Http404):
            logger.warning("request_not_found method=%s path=%s", request.method, path)
        elif isinstance(exception, PermissionDenied):
            logger.warning("request_forbidden method=%s path=%s", request.method, path)
        elif isinstance(exception, SuspiciousOperation):
            logger.warning(
                "request_suspicious method=%s path=%s error=%s",
                request.method,
                path,
                type(exception).__name__,
            )
        else:
            logger.exception(
                "request_exception method=%s path=%s error=%s",
                request.method,
                path,
                type(exception).__name__,
            )
        clear_request_context()
        return None
