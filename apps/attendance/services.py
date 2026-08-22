from __future__ import annotations

import hashlib
import secrets
from decimal import Decimal

from django.core.signing import TimestampSigner
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import CourseEnrollment, DiplomaEnrollment, Semester

from .constants import (
    ATTENDANCE_STATUS_EXCUSED,
    ATTENDANCE_STATUS_LATE,
    ATTENDANCE_STATUS_PRESENT,
    SESSION_STATUS_CLOSED,
    SESSION_STATUS_OPEN,
    SESSION_STATUS_SCHEDULED,
)
from .models import AttendanceCode, AttendanceRecord, AttendanceSession

_salt = "attendance-qr"
_signer = TimestampSigner(salt=_salt)
_QR_MAX_AGE_SECONDS = 90 * 24 * 3600  # 90 days


class AttendanceScanError(ValueError):
    """Stable domain result codes for the scanner (P7-01).

    ``code`` is one of: invalid_token, no_enrollment, wrong_scope,
    session_not_open, outside_window, enrollment_inactive, enrollment_expired.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def get_or_create_attendance_code_for_enrollment(
    enrollment: DiplomaEnrollment | CourseEnrollment,
    actor=None,
) -> AttendanceCode:
    if isinstance(enrollment, DiplomaEnrollment):
        existing = AttendanceCode.objects.filter(
            diploma_enrollment=enrollment, active=True
        ).first()
    else:
        existing = AttendanceCode.objects.filter(
            course_enrollment=enrollment, active=True
        ).first()

    if existing:
        return existing

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    code = AttendanceCode.objects.create(
        student=enrollment.student,
        diploma_enrollment=enrollment if isinstance(enrollment, DiplomaEnrollment) else None,
        course_enrollment=enrollment if isinstance(enrollment, CourseEnrollment) else None,
        token_hash=token_hash,
        display_code=token_hash[:8],
        created_by=actor,
    )

    return code


def build_attendance_qr_payload(attendance_code: AttendanceCode) -> str:
    return f"attendance:{_signer.sign(str(attendance_code.pk))}"


@transaction.atomic
def open_attendance_session(
    target,
    *,
    semester=None,
    open_at,
    close_at,
    actor,
    title="",
) -> AttendanceSession:
    if open_at >= close_at:
        raise ValueError(_("open_at must be before close_at."))

    now = timezone.now()
    status = SESSION_STATUS_SCHEDULED
    opened_by = None

    if open_at <= now:
        status = SESSION_STATUS_OPEN
        opened_by = actor

    session = AttendanceSession.objects.create(
        cohort=target if hasattr(target, "diploma") else None,
        course_version=target if hasattr(target, "course") else None,
        semester=semester,
        date=open_at.date(),
        title=title,
        status=status,
        open_at=open_at,
        close_at=close_at,
        opened_by=opened_by,
        created_by=actor,
    )

    return session


@transaction.atomic
def open_session(session: AttendanceSession, actor) -> AttendanceSession:
    if session.status != SESSION_STATUS_SCHEDULED:
        raise ValueError(_("Only a scheduled session can be opened."))
    session.status = SESSION_STATUS_OPEN
    session.opened_by = actor
    session.open_at = timezone.now()
    session.save(update_fields=["status", "opened_by", "open_at", "updated_at"])
    return session


@transaction.atomic
def close_attendance_session(session: AttendanceSession, actor) -> AttendanceSession:
    if session.status != SESSION_STATUS_OPEN:
        raise ValueError(_("Only an open session can be closed."))

    session.status = SESSION_STATUS_CLOSED
    session.closed_by = actor
    session.close_at = timezone.now()
    session.save(update_fields=["status", "closed_by", "close_at", "updated_at"])

    return session


@transaction.atomic
def scan_attendance_token(
    raw_token: str,
    *,
    session: AttendanceSession,
    scanned_by,
    now=None,
) -> tuple[AttendanceRecord, bool]:
    now = now or timezone.now()

    # Lock session row to prevent TOCTOU between status check and record creation
    locked_session = AttendanceSession.objects.select_for_update().get(pk=session.pk)

    if locked_session.status != SESSION_STATUS_OPEN:
        raise AttendanceScanError("session_not_open", _("Attendance session is not open."))
    if not (locked_session.open_at <= now <= locked_session.close_at):
        raise AttendanceScanError("outside_window", _("Scanning is outside the session time window."))

    # raw_token is the signed payload — unsign to get the pk (with expiry)
    try:
        pk = int(_signer.unsign(raw_token, max_age=_QR_MAX_AGE_SECONDS))
    except Exception:
        raise AttendanceScanError("invalid_token", _("Invalid or tampered attendance token.")) from None
    try:
        code = AttendanceCode.objects.get(pk=pk, active=True)
    except AttendanceCode.DoesNotExist:
        raise AttendanceScanError("invalid_token", _("Invalid attendance token.")) from None

    enrollment = code.diploma_enrollment or code.course_enrollment
    if enrollment is None:
        raise AttendanceScanError("no_enrollment", _("Attendance code is not linked to an enrollment."))

    # P7-01: exact active, non-expired enrollment is required. A persisted
    # status that P5-04 has not yet normalized is never the authorization
    # boundary — access is denied here at scan time.
    if enrollment.status != ENROLLMENT_STATUS_ACTIVE:
        raise AttendanceScanError("enrollment_inactive", _("The student's enrollment is not active."))
    if enrollment.expires_at and now >= enrollment.expires_at:
        raise AttendanceScanError("enrollment_expired", _("The student's enrollment has expired."))

    if isinstance(enrollment, DiplomaEnrollment):
        if not session.cohort_id:
            raise AttendanceScanError("wrong_scope", _("Session is not for a diploma cohort."))
        if enrollment.cohort_id != session.cohort_id:
            raise AttendanceScanError("wrong_scope", _("Attendance code does not match this session."))
        record_kwargs = {"diploma_enrollment": enrollment, "course_enrollment": None}
    else:
        if not session.course_version_id:
            raise AttendanceScanError("wrong_scope", _("Session is not for a course."))
        if enrollment.course_version_id != session.course_version_id:
            raise AttendanceScanError("wrong_scope", _("Attendance code does not match this session."))
        record_kwargs = {"diploma_enrollment": None, "course_enrollment": enrollment}

    record, created = AttendanceRecord.objects.get_or_create(
        session=locked_session,
        defaults={
            "student": code.student,
            "scanned_by": scanned_by,
            "attendance_code": code,
            "status": ATTENDANCE_STATUS_PRESENT,
        },
        **record_kwargs,
    )

    return record, not created


def _build_attendance_summary(total_sessions, present_count, late_count, excused_count) -> dict:
    """One canonical attendance summary projection (PERF-004).

    Shared by the single-enrollment and batch paths so the semantics are
    identical everywhere: the denominator counts only sessions whose
    attendance window has ended (close_at passed) or that are already closed
    (L4); scheduled and open future sessions must not lower attendance before
    they happen.
    """
    total_attended = present_count + late_count
    attendance_percent = (
        round((total_attended / total_sessions) * 100, 2) if total_sessions > 0 else 0.0
    )

    return {
        "total_sessions": total_sessions,
        "present_count": present_count,
        "late_count": late_count,
        "excused_count": excused_count,
        "total_attended": total_attended,
        "attendance_percent": attendance_percent,
    }


def _ended_or_closed_sessions(cohort_ids, semester, now):
    return AttendanceSession.objects.filter(
        cohort_id__in=cohort_ids,
        semester=semester,
    ).filter(
        Q(status=SESSION_STATUS_CLOSED) | Q(close_at__lte=now),
    )


def get_attendance_summary(diploma_enrollment, semester) -> dict:
    """Attendance summary for one enrollment/semester (single aggregate path)."""
    now = timezone.now()
    sessions = _ended_or_closed_sessions(
        [diploma_enrollment.cohort_id], semester, now,
    )

    counts = AttendanceRecord.objects.filter(
        session__in=sessions,
        diploma_enrollment=diploma_enrollment,
    ).aggregate(
        present_count=Count("id", filter=Q(status=ATTENDANCE_STATUS_PRESENT)),
        late_count=Count("id", filter=Q(status=ATTENDANCE_STATUS_LATE)),
        excused_count=Count("id", filter=Q(status=ATTENDANCE_STATUS_EXCUSED)),
    )

    return _build_attendance_summary(
        sessions.count(),
        counts["present_count"],
        counts["late_count"],
        counts["excused_count"],
    )


def get_attendance_summaries(diploma_enrollments, semester) -> dict[int, dict]:
    """Attendance summaries for many enrollments in ONE set-based pass.

    Returns ``{diploma_enrollment_id: summary_dict}``. Uses one grouped
    aggregate for session totals (per cohort) and one grouped aggregate for
    record counts (per enrollment/status), then builds each summary through the
    same ``_build_attendance_summary`` projection used by the single path.
    """
    enrollments = list(diploma_enrollments)
    if not enrollments:
        return {}

    now = timezone.now()
    sessions = _ended_or_closed_sessions(
        {e.cohort_id for e in enrollments}, semester, now,
    )

    session_totals = {
        row["cohort_id"]: row["total"]
        for row in sessions.values("cohort_id").annotate(total=Count("id"))
    }

    record_counts = {}
    for row in AttendanceRecord.objects.filter(
        session__in=sessions,
        diploma_enrollment_id__in=[e.id for e in enrollments],
    ).values("diploma_enrollment_id", "status").annotate(count=Count("id")):
        record_counts.setdefault(row["diploma_enrollment_id"], {})[row["status"]] = row["count"]

    summaries = {}
    for enrollment in enrollments:
        counts = record_counts.get(enrollment.id, {})
        summaries[enrollment.id] = _build_attendance_summary(
            session_totals.get(enrollment.cohort_id, 0),
            counts.get(ATTENDANCE_STATUS_PRESENT, 0),
            counts.get(ATTENDANCE_STATUS_LATE, 0),
            counts.get(ATTENDANCE_STATUS_EXCUSED, 0),
        )
    return summaries


def get_attendance_evaluation_percent(
    enrollment: DiplomaEnrollment,
    semester: Semester,
    required_sessions: int | None = None,
    summary: dict | None = None,
) -> Decimal | None:
    """Canonical attendance percent for one enrollment/semester (DECISION §13).

    With ``required_sessions`` > 0 the percent is recorded attendances out of
    the required number (e.g. 7 of 10 ⇒ 70%), capped at 100 and quantized to
    0.01. Otherwise it falls back to attended / ended-closed sessions (the
    ``attendance_percent`` from the summary). Returns ``None`` when no
    countable session exists, so the component is not applicable. Accepts a
    precomputed ``summary`` so batch callers reuse the set-based path.
    """
    if summary is None:
        summary = get_attendance_summary(enrollment, semester)
    if not summary["total_sessions"]:
        return None
    if required_sessions and required_sessions > 0:
        attended = Decimal(summary["total_attended"])
        return min(
            (attended / Decimal(required_sessions) * Decimal("100")).quantize(Decimal("0.01")),
            Decimal("100"),
        )
    return Decimal(str(summary["attendance_percent"]))
