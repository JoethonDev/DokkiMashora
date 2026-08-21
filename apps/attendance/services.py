from __future__ import annotations

import hashlib
import secrets

from django.core.signing import TimestampSigner
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import CourseEnrollment, DiplomaEnrollment

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


def get_attendance_summary(diploma_enrollment, semester) -> dict:
    """Attendance summary for one enrollment/semester.

    The denominator counts only sessions whose attendance window has ended
    (close_at passed) or that are already closed (L4); scheduled and open
    future sessions must not lower attendance before they happen.
    """
    now = timezone.now()
    sessions = AttendanceSession.objects.filter(
        cohort=diploma_enrollment.cohort,
        semester=semester,
    ).filter(
        Q(status=SESSION_STATUS_CLOSED) | Q(close_at__lte=now),
    )

    total_sessions = sessions.count()
    records = AttendanceRecord.objects.filter(
        session__in=sessions,
        diploma_enrollment=diploma_enrollment,
    )

    present_count = records.filter(status=ATTENDANCE_STATUS_PRESENT).count()
    late_count = records.filter(status=ATTENDANCE_STATUS_LATE).count()
    excused_count = records.filter(status=ATTENDANCE_STATUS_EXCUSED).count()
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
