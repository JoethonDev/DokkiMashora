from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from django.contrib.auth import get_user_model

from apps.accounts.constants import GROUP_ADMIN, GROUP_SUPER_ADMIN
from apps.core.permissions import role_required, safe_date_str, safe_int

User = get_user_model()

from .models import AuditEvent
from .selectors import get_audit_events

ROLE_AUDIT_STAFF = (GROUP_SUPER_ADMIN, GROUP_ADMIN)


@login_required
@role_required(*ROLE_AUDIT_STAFF)
def audit_list(request):
    action = request.GET.get("action", "")
    actor_id = request.GET.get("actor_id")
    target_type = request.GET.get("target_type", "")
    raw_date_from = request.GET.get("date_from", "")
    raw_date_to = request.GET.get("date_to", "")
    date_from = safe_date_str(raw_date_from)
    date_to = safe_date_str(raw_date_to)
    events = get_audit_events(
        action=action or None,
        actor_id=safe_int(actor_id),
        target_type=target_type or None,
        date_from=date_from,
        date_to=date_to,
        days=None,
    )
    from .constants import AUDIT_ACTION_CHOICES
    actor_choices = User.objects.filter(is_staff=True, is_active=True).order_by("full_name")
    return render(
        request,
        "audit/admin_list.html",
        {
            "events": events,
            "action_choices": AUDIT_ACTION_CHOICES,
            "actor_choices": actor_choices,
            "current_action": action,
            "current_actor_id": actor_id,
            "current_target_type": target_type,
            "current_date_from": raw_date_from,
            "current_date_to": raw_date_to,
        },
    )


@login_required
@role_required(*ROLE_AUDIT_STAFF)
def audit_detail(request, event_id):
    event = get_object_or_404(
        AuditEvent.objects.select_related("actor"), id=event_id
    )
    return render(request, "audit/admin_detail.html", {"event": event})
