"""Structured request/task context for searchable container logs (Dozzle).

Dozzle reads Docker's stdout/stderr stream. Every log line is a single
key=value record with the same request ID used by the audit trail, so an IT
operator can search one ID across nginx, Django, Celery, and the audit UI.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from contextvars import ContextVar
from uuid import uuid4

_REQUEST_ID: ContextVar[str] = ContextVar("log_request_id", default="")
_REQUEST_METHOD: ContextVar[str] = ContextVar("log_request_method", default="")
_REQUEST_PATH: ContextVar[str] = ContextVar("log_request_path", default="")
_REQUEST_USER: ContextVar[str] = ContextVar("log_request_user", default="")
_REQUEST_IP: ContextVar[str] = ContextVar("log_request_ip", default="")
_REQUEST_STATUS: ContextVar[str] = ContextVar("log_request_status", default="")
_REQUEST_DURATION: ContextVar[str] = ContextVar("log_request_duration", default="")
_TASK_ID: ContextVar[str] = ContextVar("log_task_id", default="")
_TASK_NAME: ContextVar[str] = ContextVar("log_task_name", default="")

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_TOKEN_PATH_MARKERS = ("/activate/", "/documents/upload/", "/forms/fill/")


def new_request_id() -> str:
    return uuid4().hex


def normalize_request_id(value: str | None) -> str:
    candidate = (value or "").strip()
    if candidate and _SAFE_REQUEST_ID.fullmatch(candidate):
        return candidate
    return new_request_id()


def redact_request_path(path: str | None) -> str:
    """Keep logs useful without persisting activation/upload tokens."""
    value = str(path or "")
    for marker in _TOKEN_PATH_MARKERS:
        if marker in value:
            return value.split(marker, 1)[0] + marker + "[redacted]/"
    return re.sub(r"/[A-Za-z0-9_-]{24,}(?=/|$)", "/[redacted]", value)[:255]


def clean_ip(value: str | None) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return ""
    if candidate.count(":") == 1 and "." in candidate:
        candidate = candidate.split(":")[0]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return ""


class RequestContextFilter(logging.Filter):
    """Attach stable request/task fields to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _REQUEST_ID.get() or "-"
        record.request_method = _REQUEST_METHOD.get() or "-"
        record.request_path = _REQUEST_PATH.get() or "-"
        record.user_label = _REQUEST_USER.get() or "-"
        record.client_ip = _REQUEST_IP.get() or "-"
        record.status_code = _REQUEST_STATUS.get() or "-"
        record.duration_ms = _REQUEST_DURATION.get() or "-"
        record.task_id = _TASK_ID.get() or "-"
        record.task_name = _TASK_NAME.get() or "-"
        return True


def get_request_id() -> str:
    return _REQUEST_ID.get()


def set_request_context(
    *,
    request_id: str,
    method: str,
    path: str,
    user_label: str,
    ip_address: str,
) -> None:
    _REQUEST_ID.set(request_id)
    _REQUEST_METHOD.set(method or "-")
    _REQUEST_PATH.set(redact_request_path(path))
    _REQUEST_USER.set(user_label or "-")
    _REQUEST_IP.set(ip_address or "-")


def set_request_result(*, status_code: int, duration_ms: int) -> None:
    _REQUEST_STATUS.set(str(status_code))
    _REQUEST_DURATION.set(str(duration_ms))


def clear_request_context() -> None:
    for variable in (
        _REQUEST_ID,
        _REQUEST_METHOD,
        _REQUEST_PATH,
        _REQUEST_USER,
        _REQUEST_IP,
        _REQUEST_STATUS,
        _REQUEST_DURATION,
    ):
        variable.set("")


def set_task_context(*, task_id: str, task_name: str) -> None:
    _TASK_ID.set(task_id or "-")
    _TASK_NAME.set(task_name or "-")


def clear_task_context() -> None:
    _TASK_ID.set("")
    _TASK_NAME.set("")
