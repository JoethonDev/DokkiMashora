from __future__ import annotations

from datetime import timedelta

from django.db.models import QuerySet
from django.utils import timezone

from .models import AuditEvent


def get_audit_events(
    action: str | None = None,
    actor_id: int | None = None,
    target_type: str | None = None,
    days: int | None = 365,
    date_from: str | None = None,
    date_to: str | None = None,
) -> QuerySet[AuditEvent]:
    qs = AuditEvent.objects.select_related("actor").all()
    if action:
        qs = qs.filter(action=action)
    if actor_id:
        qs = qs.filter(actor_id=actor_id)
    if target_type:
        qs = qs.filter(target_type=target_type)
    if days:
        qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=days))
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    return qs.order_by("-created_at")
