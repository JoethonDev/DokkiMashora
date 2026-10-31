"""Shared attendance query predicates for staff workspaces."""

from django.db.models import Q
from django.utils import timezone

from .constants import SESSION_STATUS_OPEN, SESSION_STATUS_SCHEDULED
from .models import AttendanceRecord
from .services import attendance_record_exclusion_reason


def effective_overdue_session_q(now=None) -> Q:
    """Sessions needing attention once their configured scan window has ended."""
    now = now or timezone.now()
    return Q(
        status__in=(SESSION_STATUS_SCHEDULED, SESSION_STATUS_OPEN),
        close_at__lte=now,
    )


def effective_overdue_period_q(now=None) -> Q:
    """Periods needing attention once their configured window has ended."""
    now = now or timezone.now()
    return Q(
        status__in=(SESSION_STATUS_SCHEDULED, SESSION_STATUS_OPEN),
        ends_at__lte=now,
    )


def student_attendance_history_queryset(student_profile):
    """Own-record-only attendance history with the display relations loaded."""
    return (
        AttendanceRecord.objects.filter(student=student_profile)
        .filter(
            Q(session__course_version__course__attendance_enabled=True)
            | Q(session__cohort__diploma__attendance_enabled=True)
            | Q(session__period__cohort__diploma__attendance_enabled=True)
        )
        .select_related(
            "session__period__cohort__diploma",
            "session__period__semester__diploma",
            "session__course_version__course",
        )
        .order_by("-scanned_at", "-pk")
    )


def annotate_attendance_history_records(records):
    """Mark retained records as countable or explain why they are excluded."""
    for record in records:
        record.exclusion_reason = attendance_record_exclusion_reason(record)
        record.is_countable = record.exclusion_reason is None
    return records
