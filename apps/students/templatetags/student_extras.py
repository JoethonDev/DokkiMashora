"""Template tags and filters for the student profile app."""

from __future__ import annotations

from django import template
from django.utils.translation import gettext_lazy as _

register = template.Library()

# Domain -> status value -> badge tone
_STATUS_TONE_MAP: dict[str, dict[str, str]] = {
    "application": {
        "pending": "warning",
        "under_review": "info",
        "approved": "brand",
        "rejected": "danger",
        "activated": "success",
    },
    "enrollment": {
        "active": "success",
        "expired": "neutral",
        "restricted": "danger",
        "pending": "warning",
    },
    "assessment_attempt": {
        "assigned": "info",
        "in_progress": "info",
        "submitted": "warning",
        "under_review": "warning",
        "graded": "brand",
        "released": "success",
        "expired": "neutral",
        "cancelled": "danger",
    },
    "homework": {
        "uploaded": "info",
        "submitted": "warning",
        "under_review": "warning",
        "accepted": "success",
        "needs_revision": "warning",
        "rejected": "danger",
        "graded": "brand",
        "released": "success",
    },
    "assignment": {
        "assigned": "info",
        "in_progress": "info",
        "completed": "brand",
        "graded": "success",
    },
    "semester_progress": {
        "not_started": "neutral",
        "in_progress": "info",
        "under_review": "warning",
        "passed": "success",
        "failed": "danger",
        "promoted": "success",
        "demoted": "danger",
        "locked": "danger",
        "retake_required": "warning",
        "completed": "success",
    },
    "diploma_completion": {
        "active": "success",
        "completed_requirements": "brand",
        "final_exam_pending": "warning",
        "final_exam_under_review": "warning",
        "completed": "success",
        "graduated": "success",
        "locked": "danger",
        "withdrawn": "neutral",
    },
    "email": {
        "queued": "info",
        "sent": "success",
        "failed": "danger",
    },
}


@register.filter
def status_tone(status_value: str, domain: str) -> str:
    """Map a domain-specific database status value to a badge tone.

    Usage in templates::

        {{ obj.status|status_tone:"application" }}

    Returns ``"neutral"`` when the status value or domain is unknown.
    """
    domain_map = _STATUS_TONE_MAP.get(domain, {})
    return domain_map.get(status_value, "neutral")


@register.filter
def student_safe_reason(decision):
    """Render a student-safe summary for a PromotionDecision instead of raw reason text.

    The raw ``reason`` field may contain staff/internal notes; this filter
    maps the decision type to a generic student-visible description.
    """
    mapping = {
        "auto_promote": _("Promoted automatically based on semester performance."),
        "manual_promote": _("Promoted to the next semester."),
        "demote": _("Required to repeat the semester."),
        "override_pass": _("Pass status granted by administration."),
        "override_fail": _("Required to retake the semester."),
        "lock": _("Progress has been temporarily paused."),
        "unlock": _("Progress has been resumed."),
        "retake_assigned": _("Retake has been assigned."),
        "retake_cleared": _("Retake has been completed."),
        "graduate": _("All requirements met — eligible for graduation."),
        "complete": _("Diploma program completed."),
    }
    return mapping.get(decision.decision_type, _("Decision recorded."))
