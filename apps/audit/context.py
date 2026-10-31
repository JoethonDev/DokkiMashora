"""Request-scoped audit context.

The context lets services record IP/correlation/request metadata without
threading the ``request`` object through every business function. It is set by
``AuditRequestMiddleware`` and is safe to be absent (management commands,
Celery tasks, direct service calls).
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from uuid import uuid4

from apps.core.logging import clean_ip, get_request_id

_AUDIT_REQUEST_CONTEXT: ContextVar[AuditRequestContext | None] = ContextVar(
    "audit_request_context",
    default=None,
)


@dataclass
class AuditRequestContext:
    request_id: str
    method: str
    path: str
    ip_address: str | None = None
    named_event: bool = False


def new_request_id() -> str:
    return uuid4().hex


def client_ip(request) -> str | None:
    """Return a validated client IP, respecting the nginx proxy headers."""
    candidates = [
        request.META.get("HTTP_X_REAL_IP"),
        (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip(),
        request.META.get("REMOTE_ADDR"),
    ]
    for candidate in candidates:
        value = clean_ip(candidate)
        if value:
            return value
    return None


def begin_request(request) -> tuple[AuditRequestContext, Token]:
    context = AuditRequestContext(
        request_id=get_request_id() or new_request_id(),
        method=request.method,
        path=(request.path or "")[:255],
        ip_address=client_ip(request),
    )
    token = _AUDIT_REQUEST_CONTEXT.set(context)
    return context, token


def end_request(token: Token) -> None:
    _AUDIT_REQUEST_CONTEXT.reset(token)


def get_request_context() -> AuditRequestContext | None:
    return _AUDIT_REQUEST_CONTEXT.get()


def mark_named_event() -> None:
    context = get_request_context()
    if context is not None:
        context.named_event = True
