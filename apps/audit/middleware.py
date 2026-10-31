"""Request-level safety net for mutating actions.

Named business events are preferred and carry the detailed before/after
payload. This middleware only appends an event when a POST/PUT/PATCH/DELETE
completed without a named event, so no staff mutation disappears from the
audit trail while avoiding duplicate rows for well-instrumented workflows.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.http import Http404
from django.urls import NoReverseMatch, reverse
from django.utils.deprecation import MiddlewareMixin

from apps.core.logging import redact_request_path
from apps.core.permissions import is_student

from .constants import (
    ACTION_REQUEST_MUTATION,
    OUTCOME_DENIED,
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    SECTION_ACADEMICS,
    SECTION_ACCOUNTS,
    SECTION_ADMISSIONS,
    SECTION_ASSESSMENTS,
    SECTION_ATTENDANCE,
    SECTION_CERTIFICATES,
    SECTION_COMMUNICATIONS,
    SECTION_PAYMENTS,
    SECTION_PROGRESSION,
    SECTION_SETTINGS,
    SOURCE_PUBLIC,
    SOURCE_USER,
)
from .context import begin_request, end_request
from .services import record_audit_event

logger = logging.getLogger(__name__)

_AUDITED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_SKIP_PATH_PREFIXES = (
    "/healthz",
    "/deployment/health/",
    "/static/",
    "/media/",
    "/admin/",
    "/i18n/setlang/",
)
_SKIP_URL_NAMES = {
    "set_language",
    "login",
    "logout",
    "post_login",
    "activate",
    "resend_activation",
    "send_my_activation_email",
    "fill_intake_form",
    "document_upload",
    "attendance:attendance_scan",
    "attendance:attendance_scan_api",
}
_NAMESPACE_SECTIONS = {
    "applications": SECTION_ADMISSIONS,
    "students": SECTION_ADMISSIONS,
    "entrance_exams": SECTION_ADMISSIONS,
    "intake_forms": SECTION_ADMISSIONS,
    "academics": SECTION_ACADEMICS,
    "assessments": SECTION_ASSESSMENTS,
    "attendance": SECTION_ATTENDANCE,
    "progression": SECTION_PROGRESSION,
    "payments": SECTION_PAYMENTS,
    "certificates": SECTION_CERTIFICATES,
    "communications": SECTION_COMMUNICATIONS,
    "accounts": SECTION_ACCOUNTS,
    "core": SECTION_SETTINGS,
    "audit": SECTION_SETTINGS,
}


class AuditRequestMiddleware(MiddlewareMixin):
    """Append a fallback audit event for un-instrumented mutations."""

    def process_request(self, request):
        context, token = begin_request(request)
        request._audit_context = context
        request._audit_context_token = token
        return None

    def process_response(self, request, response):
        context = getattr(request, "_audit_context", None)
        token = getattr(request, "_audit_context_token", None)
        try:
            self._maybe_record(request, response, context)
        finally:
            if token is not None:
                end_request(token)
            request._audit_context = None
            request._audit_context_token = None
        return response

    def process_exception(self, request, exception):
        context = getattr(request, "_audit_context", None)
        token = getattr(request, "_audit_context_token", None)
        try:
            if isinstance(exception, Http404):
                return None
            if isinstance(exception, PermissionDenied):
                status_code = 403
                outcome = OUTCOME_DENIED
            elif isinstance(exception, SuspiciousOperation):
                status_code = 400
                outcome = OUTCOME_FAILURE
            else:
                status_code = 500
                outcome = OUTCOME_FAILURE
            self._record(
                request,
                context,
                status_code=status_code,
                outcome=outcome,
                exception=exception,
            )
        finally:
            if token is not None:
                end_request(token)
            request._audit_context = None
            request._audit_context_token = None
        return None

    def _maybe_record(self, request, response, context):
        if context is None:
            return
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code == 400:
            return
        if status_code >= 400 and status_code not in (403,) and status_code < 500:
            return
        if status_code == 401:
            return
        if getattr(context, "named_event", False) and status_code < 400:
            return
        if 300 <= status_code < 400:
            location = response.get("Location", "") if hasattr(response, "get") else ""
            target_path = urlsplit(location).path if location else ""
            if not target_path or target_path == request.path:
                return
            try:
                login_path = reverse(settings.LOGIN_URL)
            except NoReverseMatch:
                login_path = str(settings.LOGIN_URL)
            if target_path == login_path:
                return
        # A normal invalid form re-renders with 200 and no mutation. Treat a
        # 200 POST as a mutation only when the response explicitly confirms an
        # inline action (HX-Trigger / HX-Redirect) or the request is not a
        # browser form submission. Successful full-page forms normally redirect.
        if (
            200 <= status_code < 300
            and request.method == "POST"
            and not response.has_header("HX-Trigger")
            and not response.has_header("HX-Redirect")
            and request.headers.get("HX-Request-Type") != "partial"
        ):
            return
        outcome = OUTCOME_SUCCESS
        if status_code == 403:
            outcome = OUTCOME_DENIED
        elif status_code >= 400:
            outcome = OUTCOME_FAILURE
        self._record(request, context, status_code=status_code, outcome=outcome)

    def _record(self, request, context, *, status_code, outcome, exception=None):
        if request.method not in _AUDITED_METHODS:
            return
        match = getattr(request, "resolver_match", None)
        url_name = getattr(match, "url_name", "") if match is not None else ""
        if url_name in _SKIP_URL_NAMES:
            return
        path = request.path or ""
        if any(path.startswith(prefix) for prefix in _SKIP_PATH_PREFIXES):
            return
        user = getattr(request, "user", None)
        authenticated = bool(getattr(user, "is_authenticated", False))
        if not authenticated:
            return
        if not getattr(user, "is_superuser", False) and is_student(user):
            return

        namespace = getattr(match, "namespace", "") if match is not None else ""
        section = _NAMESPACE_SECTIONS.get(namespace, SECTION_SETTINGS)
        target_id = ""
        if match is not None:
            kwargs = match.kwargs or {}
            for key in ("pk", "id", "object_id"):
                if kwargs.get(key) not in (None, ""):
                    target_id = str(kwargs[key])
                    break
            if not target_id:
                for key, value in kwargs.items():
                    if key.endswith("_id") and value not in (None, ""):
                        target_id = str(value)
                        break

        metadata = {
            "url_name": url_name,
            "method": request.method,
            "status": status_code,
            "path": redact_request_path(path),
        }
        if exception is not None:
            metadata["error"] = type(exception).__name__
        try:
            record_audit_event(
                user if authenticated else None,
                ACTION_REQUEST_MUTATION,
                target_type=namespace or "request",
                target_id=target_id,
                metadata=metadata,
                section=section,
                target_label=url_name.replace("_", " ") if url_name else "",
                source=SOURCE_USER if authenticated else SOURCE_PUBLIC,
                outcome=outcome,
                correlation_id=context.request_id,
            )
        except Exception:  # pragma: no cover - never break the response on audit failure
            logger.exception("Could not append fallback audit event for %s %s", request.method, path)
