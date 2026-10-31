from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, Q, QuerySet
from django.utils import timezone

from apps.core.dates import aware_date_bounds

from .constants import (
    AUDIT_ACTION_CHOICES,
    AUDIT_SECTION_CHOICES,
    TARGET_TYPE_LABELS,
)
from .labels import action_section, target_type_label
from .models import AuditEvent


def _text_search(q: str) -> Q:
    return (
        Q(search_text__icontains=q)
        | Q(actor_label__icontains=q)
        | Q(target_label__icontains=q)
        | Q(subject_label__icontains=q)
        | Q(action__icontains=q)
        | Q(correlation_id__icontains=q)
    )


def get_audit_events(
    action: str | None = None,
    section: str | None = None,
    actor: str | None = None,
    actor_id: int | None = None,
    target_type: str | None = None,
    source: str | None = None,
    outcome: str | None = None,
    search: str | None = None,
    days: int | None = 30,
    date_from: str | None = None,
    date_to: str | None = None,
) -> QuerySet[AuditEvent]:
    qs = AuditEvent.objects.select_related("actor").defer("search_text").all()
    if action:
        qs = qs.filter(action=action)
    if section:
        qs = qs.filter(section=section)
    if actor_id:
        qs = qs.filter(actor_id=actor_id)
    if actor:
        qs = qs.filter(
            Q(actor_label__icontains=actor)
            | Q(actor__full_name__icontains=actor)
            | Q(actor__email__icontains=actor)
        )
    if target_type:
        qs = qs.filter(target_type=target_type)
    if source:
        qs = qs.filter(source=source)
    if outcome:
        qs = qs.filter(outcome=outcome)
    if search:
        qs = qs.filter(_text_search(search))
    if days:
        qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=days))
    start, end = aware_date_bounds(date_from, date_to)
    if start:
        qs = qs.filter(created_at__gte=start)
    if end:
        qs = qs.filter(created_at__lt=end)
    return qs.order_by("-created_at")


def get_section_counts(
    *,
    days: int | None = 30,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, int]:
    qs = AuditEvent.objects.all()
    if days:
        qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=days))
    start, end = aware_date_bounds(date_from, date_to)
    if start:
        qs = qs.filter(created_at__gte=start)
    if end:
        qs = qs.filter(created_at__lt=end)
    return dict(qs.values_list("section").annotate(count=Count("id")).values_list("section", "count"))


def get_action_groups() -> list[dict[str, object]]:
    """Action choices grouped by business section for the filter dropdown."""
    grouped: dict[str, list[tuple[str, str]]] = {key: [] for key, _label in AUDIT_SECTION_CHOICES}
    for code, label in AUDIT_ACTION_CHOICES:
        grouped.setdefault(action_section(code), []).append((code, label))
    groups = []
    for key, section_label in AUDIT_SECTION_CHOICES:
        actions = grouped.get(key) or []
        if actions:
            groups.append({"key": key, "label": section_label, "actions": actions})
    return groups


def get_target_type_choices() -> list[tuple[str, str]]:
    return sorted(
        ((value, target_type_label(value)) for value in TARGET_TYPE_LABELS),
        key=lambda item: item[1].casefold(),
    )
