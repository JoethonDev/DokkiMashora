from __future__ import annotations

from typing import Any
from uuid import uuid4

from apps.core.logging import get_request_id, redact_request_path

from .constants import (
    OUTCOME_SUCCESS,
    SOURCE_SYSTEM,
)
from .context import clean_ip, get_request_context, mark_named_event
from .labels import (
    action_label,
    action_section,
    default_source,
    json_safe,
    metadata_label,
    outcome_label,
    safe_metadata,
    section_label,
    source_label,
    target_type_label,
    value_label,
)
from .models import AuditEvent


def _actor_snapshot(actor) -> tuple[str, str, str]:
    if actor is None or not getattr(actor, "is_authenticated", False):
        return "", "", ""
    label = str(getattr(actor, "full_name", "") or getattr(actor, "email", "") or actor)
    email = str(getattr(actor, "email", "") or "")
    if not hasattr(actor, "_audit_role_snapshot"):
        try:
            actor._audit_role_snapshot = str(actor.primary_role or "")
        except Exception:  # pragma: no cover - defensive for unsaved/deferred users
            actor._audit_role_snapshot = ""
    return label, email, actor._audit_role_snapshot


def _flatten(value: Any, *, prefix: str = "") -> list[str]:
    values: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = f"{prefix}.{key}" if prefix else str(key)
            values.append(metadata_label(key))
            values.extend(_flatten(nested, prefix=key_text))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            values.extend(_flatten(item, prefix=prefix))
    elif value is not None:
        raw = str(value)
        values.append(raw)
        localized = value_label(value)
        if localized != raw:
            values.append(localized)
    return values


def build_search_text(
    *,
    action: str,
    section: str,
    source: str,
    outcome: str,
    actor_label: str,
    actor_email: str,
    actor_role: str,
    target_type: str,
    target_id: str,
    target_label: str,
    subject_type: str,
    subject_id: str,
    subject_label: str,
    correlation_id: str,
    request_path: str,
    request_method: str,
    metadata: dict[str, Any] | None,
) -> str:
    parts = [
        action,
        action_label(action),
        section,
        section_label(section),
        source,
        source_label(source),
        outcome,
        outcome_label(outcome),
        actor_label,
        actor_email,
        actor_role,
        target_type,
        target_type_label(target_type),
        target_id,
        target_label,
        subject_type,
        subject_id,
        subject_label,
        correlation_id,
        request_path,
        request_method,
    ]
    parts.extend(_flatten(metadata or {}))
    text = " ".join(part for part in parts if part).casefold()
    return text[:4000]


def record_audit_event(
    actor,
    action: str,
    target_type: str = "",
    target_id: str | int = "",
    metadata: dict[str, Any] | None = None,
    ip_address: str | None = None,
    *,
    section: str = "",
    target_label: str = "",
    subject_type: str = "",
    subject_id: str | int = "",
    subject_label: str = "",
    source: str = "",
    outcome: str = "",
    correlation_id: str = "",
    request=None,
) -> AuditEvent:
    """Append one human-readable audit event.

    Backwards compatible with the original positional signature. New callers
    should pass ``section``, ``target_label``, and ``subject_*`` so the IT audit
    trail is searchable by business object and affected person.
    """
    context = get_request_context()
    actor_label, actor_email, actor_role = _actor_snapshot(actor)
    resolved_source = source or default_source(actor)
    resolved_section = section or action_section(action)
    resolved_correlation = correlation_id or (
        context.request_id if context is not None else (get_request_id() or uuid4().hex)
    )
    resolved_target_id = str(target_id)[:64] if target_id else ""
    resolved_subject_id = str(subject_id)[:64] if subject_id else ""
    safe_payload = json_safe(safe_metadata(metadata or {}))

    event = AuditEvent.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        actor_label=actor_label[:255],
        actor_role=actor_role[:64],
        action=action,
        section=resolved_section,
        target_type=str(target_type or "")[:64],
        target_id=resolved_target_id,
        target_label=str(target_label or "")[:255],
        subject_type=str(subject_type or "")[:64],
        subject_id=resolved_subject_id,
        subject_label=str(subject_label or "")[:255],
        source=resolved_source,
        outcome=outcome or OUTCOME_SUCCESS,
        correlation_id=resolved_correlation[:64],
        request_method=(context.method if context is not None else "")[:8],
        request_path=redact_request_path(context.path) if context is not None else "",
        metadata=safe_payload,
        search_text=build_search_text(
            action=action,
            section=resolved_section,
            source=resolved_source,
            outcome=outcome or OUTCOME_SUCCESS,
            actor_label=actor_label,
            actor_email=actor_email,
            actor_role=actor_role,
            target_type=str(target_type or ""),
            target_id=resolved_target_id,
            target_label=str(target_label or ""),
            subject_type=str(subject_type or ""),
            subject_id=resolved_subject_id,
            subject_label=str(subject_label or ""),
            correlation_id=resolved_correlation,
            request_path=redact_request_path(context.path) if context is not None else "",
            request_method=(context.method if context is not None else ""),
            metadata=safe_payload,
        ),
        ip_address=clean_ip(ip_address) or (context.ip_address if context is not None else None),
    )
    mark_named_event()
    return event


def record_system_event(
    action: str,
    *,
    section: str = "",
    target_type: str = "",
    target_id: str | int = "",
    target_label: str = "",
    subject_type: str = "",
    subject_id: str | int = "",
    subject_label: str = "",
    metadata: dict[str, Any] | None = None,
    outcome: str = "success",
    correlation_id: str = "",
    source: str = SOURCE_SYSTEM,
) -> AuditEvent:
    """Convenience wrapper for Celery tasks, email delivery, and commands."""
    return record_audit_event(
        None,
        action,
        target_type=target_type,
        target_id=target_id,
        metadata=metadata,
        section=section,
        target_label=target_label,
        subject_type=subject_type,
        subject_id=subject_id,
        subject_label=subject_label,
        source=source,
        outcome=outcome,
        correlation_id=correlation_id,
    )
