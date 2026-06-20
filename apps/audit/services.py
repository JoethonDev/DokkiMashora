from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.db import IntegrityError

from .models import AuditEvent

User = get_user_model()


def record_audit_event(
    actor: User | None,
    action: str,
    target_type: str = "",
    target_id: str = "",
    metadata: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> AuditEvent | None:
    try:
        event = AuditEvent.objects.create(
            actor=actor,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id else "",
            metadata=metadata or {},
            ip_address=ip_address,
        )
        return event
    except IntegrityError:
        return None
