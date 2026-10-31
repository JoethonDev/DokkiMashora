from __future__ import annotations

from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext_lazy as _

from apps.core.logging import redact_request_path
from apps.core.pagination import paginate
from apps.core.permissions import infrastructure_required, safe_date_str, safe_int

from .constants import (
    AUDIT_SECTION_CHOICES,
    AUDIT_SECTION_KEYS,
    OUTCOME_CHOICES,
    SOURCE_CHOICES,
)
from .labels import (
    action_label,
    actor_display,
    actor_role_display,
    display_metadata,
    outcome_label,
    section_label,
    source_label,
    subject_display,
    target_display,
    target_type_label,
)
from .models import AuditEvent
from .selectors import (
    get_action_groups,
    get_audit_events,
    get_section_counts,
    get_target_type_choices,
)

_PERIOD_DAYS = {
    "24h": 1,
    "7d": 7,
    "30d": 30,
    "90d": 90,
    "all": None,
}


def _prepare_event(event: AuditEvent, *, full: bool = True) -> AuditEvent:
    metadata = event.metadata
    if isinstance(metadata, dict) and metadata.get("path"):
        metadata = {**metadata, "path": redact_request_path(metadata["path"])}
    if not full and isinstance(metadata, dict):
        metadata = dict(list(metadata.items())[:3])
    event.display_metadata = display_metadata(metadata)
    event.request_path = redact_request_path(event.request_path) if event.request_path else ""
    event.action_label = action_label(event.action)
    event.section_label = section_label(event.section) if event.section else ""
    event.actor_display = actor_display(event)
    event.actor_role_display = actor_role_display(event)
    event.target_display = target_display(event)
    event.target_type_label = target_type_label(event.target_type) if event.target_type else ""
    event.subject_display = subject_display(event)
    event.source_label = source_label(event.source)
    event.outcome_label = outcome_label(event.outcome)
    return event


@infrastructure_required
def audit_list(request):
    action = request.GET.get("action", "").strip()
    section = request.GET.get("section", "").strip()
    if section not in AUDIT_SECTION_KEYS:
        section = ""
    actor = request.GET.get("actor", "").strip()
    actor_id = safe_int(request.GET.get("actor_id"))
    target_type = request.GET.get("target_type", "").strip()
    source = request.GET.get("source", "").strip()
    outcome = request.GET.get("outcome", "").strip()
    search = request.GET.get("q", "").strip()[:200]
    period = request.GET.get("period", "30d")
    if period not in _PERIOD_DAYS:
        period = "30d"
    raw_date_from = request.GET.get("date_from", "").strip()
    raw_date_to = request.GET.get("date_to", "").strip()
    date_from = safe_date_str(raw_date_from)
    date_to = safe_date_str(raw_date_to)
    days = None if (date_from or date_to) else _PERIOD_DAYS[period]

    events = get_audit_events(
        action=action or None,
        section=section or None,
        actor=actor or None,
        actor_id=actor_id,
        target_type=target_type or None,
        source=source or None,
        outcome=outcome or None,
        search=search or None,
        days=days,
        date_from=date_from,
        date_to=date_to,
    )
    section_counts = get_section_counts(days=days, date_from=date_from, date_to=date_to)
    page_obj, pagination_qs = paginate(request, events)
    for event in page_obj:
        _prepare_event(event, full=False)
    return render(
        request,
        "ui/pages/admin/audit.html",
        {
            "page_obj": page_obj,
            "pagination_qs": pagination_qs,
            "action_groups": get_action_groups(),
            "target_type_choices": get_target_type_choices(),
            "section_choices": AUDIT_SECTION_CHOICES,
            "section_counts": section_counts,
            "total_count": sum(section_counts.values()),
            "source_choices": SOURCE_CHOICES,
            "outcome_choices": OUTCOME_CHOICES,
            "period_choices": (
                ("24h", _("Last 24 hours")),
                ("7d", _("Last 7 days")),
                ("30d", _("Last 30 days")),
                ("90d", _("Last 90 days")),
                ("all", _("All time")),
            ),
            "current_action": action,
            "current_section": section,
            "current_actor": actor,
            "current_target_type": target_type,
            "current_source": source,
            "current_outcome": outcome,
            "current_search": search,
            "current_period": period,
            "current_date_from": raw_date_from,
            "current_date_to": raw_date_to,
        },
    )


@infrastructure_required
def audit_detail(request, event_id):
    event = get_object_or_404(AuditEvent.objects.select_related("actor"), id=event_id)
    _prepare_event(event)
    return render(
        request,
        "ui/pages/admin/audit_detail.html",
        {
            "event": event,
        },
    )
