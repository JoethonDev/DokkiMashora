from __future__ import annotations

import hashlib
import secrets
from decimal import Decimal

from django.core.signing import TimestampSigner
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Max, Q
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import CourseEnrollment, DiplomaEnrollment, Semester
from apps.audit.constants import (
    ACTION_ATTENDANCE_PERIOD_DAY_CREATED,
    ACTION_ATTENDANCE_PERIOD_WINDOW_UPDATED,
    ACTION_ATTENDANCE_SESSION_CLOSED,
    ACTION_ATTENDANCE_SESSION_CREATED,
    ACTION_ATTENDANCE_SESSION_DELETED,
    ACTION_ATTENDANCE_SESSION_OPENED,
    ACTION_ATTENDANCE_SESSION_WINDOW_UPDATED,
    SECTION_ATTENDANCE,
)
from apps.audit.services import record_audit_event

from .constants import (
    ATTENDANCE_STATUS_EXCUSED,
    ATTENDANCE_STATUS_LATE,
    ATTENDANCE_STATUS_PRESENT,
    SESSION_STATUS_CANCELLED,
    SESSION_STATUS_CLOSED,
    SESSION_STATUS_OPEN,
    SESSION_STATUS_SCHEDULED,
)
from .models import AttendanceCode, AttendancePeriod, AttendanceRecord, AttendanceSession

_salt = "attendance-qr"
_signer = TimestampSigner(salt=_salt)


class AttendanceScanError(ValueError):
    """Stable domain result codes for the scanner (P7-01).

    ``code`` is one of: invalid_token, session_not_found, no_enrollment,
    wrong_scope, session_not_open, outside_window, enrollment_inactive,
    enrollment_expired.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _attendance_code_key(
    enrollment: DiplomaEnrollment | CourseEnrollment,
) -> tuple[str, int]:
    return ("diploma", enrollment.pk) if isinstance(enrollment, DiplomaEnrollment) else ("course", enrollment.pk)


def _build_attendance_code(
    enrollment: DiplomaEnrollment | CourseEnrollment,
    actor=None,
) -> AttendanceCode:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    return AttendanceCode(
        student_id=enrollment.student_id,
        diploma_enrollment=enrollment if isinstance(enrollment, DiplomaEnrollment) else None,
        course_enrollment=enrollment if isinstance(enrollment, CourseEnrollment) else None,
        token_hash=token_hash,
        display_code=token_hash[:12],
        created_by=actor,
    )


@transaction.atomic
def get_or_create_attendance_code_for_enrollment(
    enrollment: DiplomaEnrollment | CourseEnrollment,
    actor=None,
) -> AttendanceCode:
    # Serialize code creation per enrollment on the enrollment row so
    # concurrent callers cannot both create an active code (RV-019). The
    # partial unique constraints still guard the DB; this lock makes the
    # check-then-create deterministic instead of racing to an IntegrityError.
    if isinstance(enrollment, DiplomaEnrollment):
        locked = DiplomaEnrollment.objects.select_for_update().get(pk=enrollment.pk)
        existing = AttendanceCode.objects.filter(
            diploma_enrollment=locked, active=True
        ).first()
    else:
        locked = CourseEnrollment.objects.select_for_update().get(pk=enrollment.pk)
        existing = AttendanceCode.objects.filter(
            course_enrollment=locked, active=True
        ).first()

    if existing:
        return existing

    code = _build_attendance_code(locked, actor=actor)
    code.save()
    return code


@transaction.atomic
def get_or_create_attendance_codes_for_enrollments(
    enrollments: list[DiplomaEnrollment | CourseEnrollment],
    actor=None,
) -> dict[tuple[str, int], AttendanceCode]:
    """Create active attendance codes for many enrollments in bounded queries."""
    requested: dict[tuple[str, int], DiplomaEnrollment | CourseEnrollment] = {}
    for enrollment in enrollments:
        if not enrollment.pk:
            raise ValueError(_("Attendance codes require saved enrollments."))
        requested[_attendance_code_key(enrollment)] = enrollment
    if not requested:
        return {}

    diploma_ids = sorted(
        enrollment_id
        for kind, enrollment_id in requested
        if kind == "diploma"
    )
    course_ids = sorted(
        enrollment_id
        for kind, enrollment_id in requested
        if kind == "course"
    )
    locked_diploma = {
        enrollment.pk: enrollment
        for enrollment in DiplomaEnrollment.objects.select_for_update()
        .filter(pk__in=diploma_ids)
        .order_by("pk")
    }
    locked_course = {
        enrollment.pk: enrollment
        for enrollment in CourseEnrollment.objects.select_for_update()
        .filter(pk__in=course_ids)
        .order_by("pk")
    }
    if set(locked_diploma) != set(diploma_ids) or set(locked_course) != set(course_ids):
        raise ValueError(_("One or more enrollments no longer exist."))

    locked_by_key: dict[tuple[str, int], DiplomaEnrollment | CourseEnrollment] = {
        ("diploma", enrollment_id): enrollment
        for enrollment_id, enrollment in locked_diploma.items()
    }
    locked_by_key.update(
        {("course", enrollment_id): enrollment for enrollment_id, enrollment in locked_course.items()}
    )

    existing: dict[tuple[str, int], AttendanceCode] = {}
    for code in AttendanceCode.objects.filter(
        Q(diploma_enrollment_id__in=diploma_ids)
        | Q(course_enrollment_id__in=course_ids),
        active=True,
    ):
        if code.diploma_enrollment_id:
            existing[("diploma", code.diploma_enrollment_id)] = code
        elif code.course_enrollment_id:
            existing[("course", code.course_enrollment_id)] = code

    missing_keys = [key for key in requested if key not in existing]
    if missing_keys:
        codes = [
            _build_attendance_code(locked_by_key[key], actor=actor)
            for key in missing_keys
        ]
        AttendanceCode.objects.bulk_create(codes, batch_size=500)
        for key, code in zip(missing_keys, codes, strict=True):
            existing[key] = code

    return existing


def build_attendance_qr_payload(attendance_code: AttendanceCode) -> str:
    return f"attendance:{_signer.sign(str(attendance_code.pk))}"


def _attendance_period_label(period: AttendancePeriod) -> str:
    """Readable period label from already-loaded scalar fields (no extra query)."""
    title = period.title or _("Attendance period")
    return f"{title} - {period.cohort.name} - {period.semester.title}"


def _attendance_session_label(
    session: AttendanceSession,
    *,
    title=None,
    session_date=None,
) -> str:
    """Readable session label without touching related objects."""
    label = session.title if title is None else title
    if session.period_id:
        label = label or f"{_('Session')} {session.session_number}"
    else:
        label = label or _("Attendance")
    date_value = session.date if session_date is None else session_date
    return f"{label} - {date_value}" if date_value else label


@transaction.atomic
def create_attendance_period(
    cohort,
    semester,
    *,
    starts_at,
    ends_at,
    actor,
    title="",
) -> AttendancePeriod:
    """Create the single diploma attendance period for a cohort/semester."""

    if not cohort.diploma.attendance_enabled:
        raise ValueError(_("Attendance is disabled for this diploma."))
    if starts_at >= ends_at:
        raise ValueError(_("The attendance period must start before it ends."))
    if semester.diploma_id != cohort.diploma_id:
        raise ValueError(_("Semester must belong to the cohort's diploma."))

    now = timezone.now()
    if now >= ends_at:
        status = SESSION_STATUS_CLOSED
    elif starts_at <= now:
        status = SESSION_STATUS_OPEN
    else:
        status = SESSION_STATUS_SCHEDULED
    try:
        with transaction.atomic():
            period = AttendancePeriod.objects.create(
                cohort=cohort,
                semester=semester,
                title=title,
                starts_at=starts_at,
                ends_at=ends_at,
                status=status,
                opened_by=actor if status == SESSION_STATUS_OPEN else None,
                created_by=actor,
            )
            # No dedicated period-created action exists. Reuse the session
            # creation code with the attendance_period target type so a created
            # period never disappears from the audit trail.
            record_audit_event(
                actor=actor,
                action=ACTION_ATTENDANCE_SESSION_CREATED,
                target_type="attendance_period",
                target_id=str(period.pk),
                section=SECTION_ATTENDANCE,
                target_label=_attendance_period_label(period),
                metadata={
                    "entity_type": "attendance_period",
                    "starts_at": period.starts_at.isoformat(),
                    "ends_at": period.ends_at.isoformat(),
                    "status": period.status,
                    "cohort_id": period.cohort_id,
                    "semester_id": period.semester_id,
                },
            )
            return period
    except IntegrityError:
        raise ValueError(
            _("An attendance period already exists for this cohort and semester.")
        ) from None


@transaction.atomic
def create_attendance_day(
    period: AttendancePeriod,
    *,
    session_date,
    open_at,
    close_at,
    actor,
    title="",
) -> AttendanceSession:
    """Create one numbered, dated attendance session inside a period."""

    if open_at >= close_at:
        raise ValueError(_("open_at must be before close_at."))

    locked_period = AttendancePeriod.objects.select_for_update().get(pk=period.pk)
    if not locked_period.cohort.diploma.attendance_enabled:
        raise ValueError(_("Attendance is disabled for this diploma."))
    if locked_period.status in (SESSION_STATUS_CLOSED, SESSION_STATUS_CANCELLED):
        raise ValueError(_("The attendance period is no longer active."))
    period_start_date = timezone.localtime(locked_period.starts_at).date()
    period_end_date = timezone.localtime(locked_period.ends_at).date()
    if session_date < period_start_date or session_date > period_end_date:
        raise ValueError(_("Session date must be inside the attendance period."))
    if open_at < locked_period.starts_at or close_at > locked_period.ends_at:
        raise ValueError(_("Session times must be inside the attendance period."))

    now = timezone.now()
    if now >= locked_period.ends_at:
        if locked_period.status in (SESSION_STATUS_SCHEDULED, SESSION_STATUS_OPEN):
            locked_period.status = SESSION_STATUS_CLOSED
            locked_period.closed_by = actor
            locked_period.save(update_fields=["status", "closed_by", "updated_at"])
        raise ValueError(_("The attendance period is no longer active."))
    if now >= close_at:
        status = SESSION_STATUS_CLOSED
    elif open_at <= now:
        status = SESSION_STATUS_OPEN
    else:
        status = SESSION_STATUS_SCHEDULED
    if (
        locked_period.status == SESSION_STATUS_SCHEDULED
        and locked_period.starts_at <= now < locked_period.ends_at
    ):
        locked_period.status = SESSION_STATUS_OPEN
        locked_period.opened_by = actor
        locked_period.save(update_fields=["status", "opened_by", "updated_at"])

    next_number = (
        locked_period.sessions.aggregate(max_number=Max("session_number"))["max_number"] or 0
    ) + 1
    try:
        session = AttendanceSession.objects.create(
            cohort=locked_period.cohort,
            semester=locked_period.semester,
            period=locked_period,
            session_number=next_number,
            date=session_date,
            title=title,
            status=status,
            open_at=open_at,
            close_at=close_at,
            opened_by=actor if status == SESSION_STATUS_OPEN else None,
            closed_by=actor if status == SESSION_STATUS_CLOSED else None,
            created_by=actor,
        )
    except IntegrityError:
        raise ValueError(
            _("An attendance session already exists for this date in the period.")
        ) from None
    record_audit_event(
        actor=actor,
        action=ACTION_ATTENDANCE_PERIOD_DAY_CREATED,
        target_type="attendance_session",
        target_id=str(session.pk),
        section=SECTION_ATTENDANCE,
        target_label=_attendance_session_label(session),
        metadata={
            "entity_type": "attendance_session",
            "period_id": session.period_id,
            "date": session.date.isoformat(),
            "open_at": session.open_at.isoformat(),
            "close_at": session.close_at.isoformat(),
            "status": session.status,
            "number": session.session_number,
        },
    )
    return session


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
    """Create a standalone course session.

    Diploma attendance must be created as a period followed by dated child
    sessions; allowing the old diploma path would reintroduce duplicates.
    """

    if hasattr(target, "diploma"):
        raise ValueError(
            _("Diploma attendance must be created as a cohort semester period.")
        )
    if not target.course.attendance_enabled:
        raise ValueError(_("Attendance is disabled for this course."))
    if open_at >= close_at:
        raise ValueError(_("open_at must be before close_at."))

    now = timezone.now()
    status = SESSION_STATUS_OPEN if open_at <= now < close_at else SESSION_STATUS_SCHEDULED
    session = AttendanceSession.objects.create(
        course_version=target,
        date=open_at.date(),
        title=title,
        status=status,
        open_at=open_at,
        close_at=close_at,
        opened_by=actor if status == SESSION_STATUS_OPEN else None,
        created_by=actor,
    )
    record_audit_event(
        actor=actor,
        action=ACTION_ATTENDANCE_SESSION_CREATED,
        target_type="attendance_session",
        target_id=str(session.pk),
        section=SECTION_ATTENDANCE,
        target_label=_attendance_session_label(session),
        metadata={
            "entity_type": "attendance_session",
            "course_id": target.course_id,
            "course_version_id": target.pk,
            "date": session.date.isoformat(),
            "open_at": session.open_at.isoformat(),
            "close_at": session.close_at.isoformat(),
            "status": session.status,
        },
    )
    return session


@transaction.atomic
def open_session(session: AttendanceSession, actor) -> AttendanceSession:
    locked = AttendanceSession.objects.select_for_update().select_related(
        "course_version__course", "cohort__diploma"
    ).get(pk=session.pk)
    if locked.course_version_id and not locked.course_version.course.attendance_enabled:
        raise ValueError(_("Attendance is disabled for this course."))
    if locked.cohort_id and not locked.cohort.diploma.attendance_enabled:
        raise ValueError(_("Attendance is disabled for this diploma."))
    if locked.status != SESSION_STATUS_SCHEDULED:
        raise ValueError(_("Only a scheduled session can be opened."))
    now = timezone.now()
    if now >= locked.close_at:
        raise ValueError(_("This attendance session has already ended."))
    period = AttendancePeriod.objects.get(pk=locked.period_id) if locked.period_id else None
    if period:
        if period.status == SESSION_STATUS_SCHEDULED:
            if now < period.starts_at or now >= period.ends_at:
                raise ValueError(_("The attendance period is not active yet."))
            period.status = SESSION_STATUS_OPEN
            period.opened_by = actor
            period.save(update_fields=["status", "opened_by", "updated_at"])
        elif period.status != SESSION_STATUS_OPEN:
            raise ValueError(_("The attendance period is no longer active."))
    locked.status = SESSION_STATUS_OPEN
    locked.opened_by = actor
    locked.save(update_fields=["status", "opened_by", "updated_at"])
    record_audit_event(
        actor=actor,
        action=ACTION_ATTENDANCE_SESSION_OPENED,
        target_type="attendance_session",
        target_id=str(locked.pk),
        section=SECTION_ATTENDANCE,
        target_label=_attendance_session_label(locked),
        metadata={
            "entity_type": "attendance_session",
            "status_before": SESSION_STATUS_SCHEDULED,
            "status_after": locked.status,
            "period_id": locked.period_id,
            "date": locked.date.isoformat(),
            "open_at": locked.open_at.isoformat(),
            "close_at": locked.close_at.isoformat(),
        },
    )
    return locked


@transaction.atomic
def close_attendance_session(session: AttendanceSession, actor) -> AttendanceSession:
    locked = AttendanceSession.objects.select_for_update().get(pk=session.pk)
    if locked.status != SESSION_STATUS_OPEN:
        raise ValueError(_("Only an open session can be closed."))

    locked.status = SESSION_STATUS_CLOSED
    locked.closed_by = actor
    locked.closed_at = timezone.now()
    locked.save(update_fields=["status", "closed_by", "closed_at", "updated_at"])
    record_audit_event(
        actor=actor,
        action=ACTION_ATTENDANCE_SESSION_CLOSED,
        target_type="attendance_session",
        target_id=str(locked.pk),
        section=SECTION_ATTENDANCE,
        target_label=_attendance_session_label(locked),
        metadata={
            "entity_type": "attendance_session",
            "status_before": SESSION_STATUS_OPEN,
            "status_after": locked.status,
            "period_id": locked.period_id,
            "date": locked.date.isoformat(),
            "closed_at": locked.closed_at.isoformat(),
        },
    )

    return locked


@transaction.atomic
def close_attendance_period(period: AttendancePeriod, actor) -> AttendancePeriod:
    """Close a period only after its currently open daily sessions are closed."""

    locked = AttendancePeriod.objects.select_for_update().get(pk=period.pk)
    if locked.status != SESSION_STATUS_OPEN:
        raise ValueError(_("Only an active attendance period can be closed."))
    if locked.sessions.filter(status=SESSION_STATUS_OPEN).exists():
        raise ValueError(_("Close all open attendance sessions before closing the period."))
    locked.status = SESSION_STATUS_CLOSED
    locked.closed_by = actor
    locked.save(update_fields=["status", "closed_by", "updated_at"])
    # No dedicated period-closed action exists; reuse the session close code
    # with the attendance_period target type.
    record_audit_event(
        actor=actor,
        action=ACTION_ATTENDANCE_SESSION_CLOSED,
        target_type="attendance_period",
        target_id=str(locked.pk),
        section=SECTION_ATTENDANCE,
        target_label=locked.title or _("Attendance period"),
        metadata={
            "entity_type": "attendance_period",
            "status_before": SESSION_STATUS_OPEN,
            "status_after": locked.status,
            "cohort_id": locked.cohort_id,
            "semester_id": locked.semester_id,
            "starts_at": locked.starts_at.isoformat(),
            "ends_at": locked.ends_at.isoformat(),
        },
    )
    return locked


@transaction.atomic
def delete_empty_attendance_session(
    session: AttendanceSession,
    actor,
) -> tuple[str, int | None]:
    """Delete only an empty session, serializing the decision with scans."""
    try:
        # Lock only the session row. Its target FKs are nullable, and PostgreSQL
        # rejects FOR UPDATE on the nullable side of the outer joins that
        # select_related() would add.
        locked = AttendanceSession.objects.select_for_update().get(pk=session.pk)
    except AttendanceSession.DoesNotExist:
        raise ValueError(_("This attendance session has already been deleted.")) from None

    if AttendanceRecord.objects.filter(session_id=locked.pk).exists():
        raise ValueError(_("This session has recorded attendance and cannot be deleted."))

    # Build the confirmation label from scalar fields only. Calling str(locked)
    # would lazy-load period/cohort/semester after the lock, adding queries.
    if locked.period_id:
        label = locked.title or f"{_('Session')} {locked.session_number}"
    else:
        label = locked.title or _("Attendance")
    info = f"{label} - {locked.date}"
    period_id = locked.period_id
    record_audit_event(
        actor=actor,
        action=ACTION_ATTENDANCE_SESSION_DELETED,
        target_type="attendance_session",
        target_id=str(locked.pk),
        section=SECTION_ATTENDANCE,
        target_label=info,
        metadata={
            "entity_type": "attendance_session",
            "title": locked.title,
            "date": str(locked.date),
            "cohort_id": locked.cohort_id,
            "course_version_id": locked.course_version_id,
            "semester_id": locked.semester_id,
            "period_id": locked.period_id,
            "number": locked.session_number,
            "status": locked.status,
        },
    )
    locked.delete()
    return info, period_id


@transaction.atomic
def update_attendance_period_window(
    period: AttendancePeriod,
    starts_at,
    ends_at,
    actor,
) -> AttendancePeriod:
    """Correct a period window without changing any child session or scan."""
    if timezone.is_naive(starts_at) or timezone.is_naive(ends_at):
        raise ValueError(_("Attendance period dates must include a time zone."))
    if starts_at >= ends_at:
        raise ValueError(_("The attendance period must start before it ends."))

    locked = AttendancePeriod.objects.select_for_update().select_related(
        "cohort", "semester",
    ).get(pk=period.pk)
    if locked.status == SESSION_STATUS_CANCELLED:
        raise ValueError(_("A cancelled attendance period cannot be edited."))
    if locked.semester.diploma_id != locked.cohort.diploma_id:
        raise ValueError(_("Semester must belong to the cohort's diploma."))

    before = {
        "starts_at": locked.starts_at.isoformat(),
        "ends_at": locked.ends_at.isoformat(),
    }
    after = {"starts_at": starts_at.isoformat(), "ends_at": ends_at.isoformat()}
    if before == after:
        return locked

    locked.starts_at = starts_at
    locked.ends_at = ends_at
    locked.save(update_fields=["starts_at", "ends_at", "updated_at"])
    record_audit_event(
        actor=actor,
        action=ACTION_ATTENDANCE_PERIOD_WINDOW_UPDATED,
        target_type="attendance_period",
        target_id=str(locked.pk),
        section=SECTION_ATTENDANCE,
        target_label=_attendance_period_label(locked),
        metadata={
            "entity_type": "attendance_period",
            "before": before,
            "after": after,
        },
    )
    return locked


@transaction.atomic
def update_attendance_session_window(
    session: AttendanceSession,
    session_date,
    open_at,
    close_at,
    title,
    actor,
) -> AttendanceSession:
    """Correct a session window while preserving its scan history."""
    if timezone.is_naive(open_at) or timezone.is_naive(close_at):
        raise ValueError(_("Attendance session dates must include a time zone."))
    if open_at >= close_at:
        raise ValueError(_("open_at must be before close_at."))

    period = None
    if session.period_id:
        # Use the same parent-before-child lock order as period session creation
        # so a concurrent period-window edit cannot validate stale boundaries.
        period = AttendancePeriod.objects.select_for_update().get(
            pk=session.period_id,
        )
    # Lock only the session row: period is nullable, so selecting it here would
    # add an outer join that PostgreSQL cannot lock with FOR UPDATE. The parent
    # period is already locked above when the session belongs to one.
    locked = AttendanceSession.objects.select_for_update().get(pk=session.pk)
    if locked.period_id != (period.pk if period else None):
        raise ValueError(_("The attendance session's period has changed; reload and try again."))
    if locked.status == SESSION_STATUS_CANCELLED:
        raise ValueError(_("A cancelled attendance session cannot be edited."))
    if period:
        if period.status == SESSION_STATUS_CANCELLED:
            raise ValueError(_("A cancelled attendance period cannot be edited."))
        period_start_date = timezone.localtime(period.starts_at).date()
        period_end_date = timezone.localtime(period.ends_at).date()
        if session_date < period_start_date or session_date > period_end_date:
            raise ValueError(_("Session date must be inside the attendance period."))
        if open_at < period.starts_at or close_at > period.ends_at:
            raise ValueError(_("Session times must be inside the attendance period."))

    before = {
        "date": locked.date.isoformat(),
        "open_at": locked.open_at.isoformat(),
        "close_at": locked.close_at.isoformat(),
        "title": locked.title,
    }
    after = {
        "date": session_date.isoformat(),
        "open_at": open_at.isoformat(),
        "close_at": close_at.isoformat(),
        "title": title,
    }
    if before == after:
        return locked

    target_label = _attendance_session_label(locked)
    locked.date = session_date
    locked.open_at = open_at
    locked.close_at = close_at
    locked.title = title
    try:
        with transaction.atomic():
            locked.save(update_fields=["date", "open_at", "close_at", "title", "updated_at"])
    except IntegrityError:
        if locked.period_id:
            raise ValueError(
                _("An attendance session already exists for this date in the period.")
            ) from None
        raise

    record_audit_event(
        actor=actor,
        action=ACTION_ATTENDANCE_SESSION_WINDOW_UPDATED,
        target_type="attendance_session",
        target_id=str(locked.pk),
        section=SECTION_ATTENDANCE,
        target_label=target_label,
        metadata={
            "entity_type": "attendance_session",
            "before": before,
            "after": after,
        },
    )
    return locked


@transaction.atomic
def scan_attendance_token(
    raw_token: str,
    *,
    session: AttendanceSession,
    scanned_by,
    now=None,
) -> tuple[AttendanceRecord, bool]:
    now = now or timezone.now()

    # Lock only the dated child session. Scans on another day in the same
    # semester must continue concurrently.
    try:
        locked_session = AttendanceSession.objects.select_for_update().select_related(
            "course_version__course", "cohort__diploma"
        ).get(pk=session.pk)
    except AttendanceSession.DoesNotExist:
        raise AttendanceScanError(
            "session_not_found",
            _("Attendance session no longer exists."),
        ) from None

    if locked_session.course_version_id and not locked_session.course_version.course.attendance_enabled:
        raise AttendanceScanError(
            "attendance_disabled",
            _("Attendance is disabled for this course."),
        )
    if locked_session.cohort_id and not locked_session.cohort.diploma.attendance_enabled:
        raise AttendanceScanError(
            "attendance_disabled",
            _("Attendance is disabled for this diploma."),
        )
    if locked_session.status != SESSION_STATUS_OPEN:
        raise AttendanceScanError("session_not_open", _("Attendance session is not open."))
    if not (locked_session.open_at <= now <= locked_session.close_at):
        raise AttendanceScanError("outside_window", _("Scanning is outside the session time window."))
    period = AttendancePeriod.objects.get(pk=locked_session.period_id) if locked_session.period_id else None
    if period:
        if period.status != SESSION_STATUS_OPEN:
            raise AttendanceScanError("session_not_open", _("The attendance period is not open."))
        if not (period.starts_at <= now <= period.ends_at):
            raise AttendanceScanError("outside_window", _("Scanning is outside the attendance period."))

    # The signed student reference has no age cutoff; authorization still
    # depends on the current code, enrollment, session, and target context.
    try:
        # The signed student reference is intentionally long-lived. Current
        # session, enrollment, and ownership checks below remain authoritative.
        pk = int(_signer.unsign(raw_token))
    except Exception:
        raise AttendanceScanError("invalid_token", _("Invalid or tampered attendance token.")) from None
    try:
        code = AttendanceCode.objects.get(pk=pk, active=True)
    except AttendanceCode.DoesNotExist:
        raise AttendanceScanError("invalid_token", _("Invalid attendance token.")) from None

    enrollment = code.diploma_enrollment or code.course_enrollment
    if enrollment is None:
        raise AttendanceScanError("no_enrollment", _("Attendance code is not linked to an enrollment."))
    if enrollment.student_id != code.student_id:
        raise AttendanceScanError("wrong_scope", _("Attendance code does not match its enrollment."))

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


def counted_attendance_record_q(record_prefix: str = "") -> Q:
    """Return the single rule used to include scans in attendance totals.

    Raw scan rows are retained as academic evidence. The current session and
    optional parent-period windows determine whether each row contributes to
    evaluation; ``record_prefix`` lets aggregate callers apply the same rule
    through a reverse relation such as ``sessions__records__``.
    """
    def path(lookup: str) -> str:
        return f"{record_prefix}{lookup}"

    return (
        Q(**{path("status__in"): (
            ATTENDANCE_STATUS_PRESENT,
            ATTENDANCE_STATUS_LATE,
            ATTENDANCE_STATUS_EXCUSED,
        )})
        & ~Q(**{path("session__status"): SESSION_STATUS_CANCELLED})
        & Q(**{path("scanned_at__gte"): F(path("session__open_at"))})
        & Q(**{path("scanned_at__lte"): F(path("session__close_at"))})
        & (
            Q(**{path("session__closed_at__isnull"): True})
            | Q(**{path("scanned_at__lte"): F(path("session__closed_at"))})
        )
        & (
            Q(**{path("session__period__isnull"): True})
            | (
                Q(**{path("scanned_at__gte"): F(path("session__period__starts_at"))})
                & Q(**{path("scanned_at__lte"): F(path("session__period__ends_at"))})
            )
        )
    )


def attendance_record_exclusion_reason(record: AttendanceRecord) -> str | None:
    """Return a translated reason when a retained scan is not countable."""
    session = record.session
    if session.status == SESSION_STATUS_CANCELLED:
        return _("The attendance session was cancelled.")
    if record.scanned_at < session.open_at:
        return _("The record is before the session opens.")
    if record.scanned_at > session.close_at:
        return _("The record is after the session closes.")

    period = session.period
    if period and record.scanned_at < period.starts_at:
        return _("The record is before the attendance period starts.")
    if period and record.scanned_at > period.ends_at:
        return _("The record is after the attendance period ends.")
    if session.closed_at and record.scanned_at > session.closed_at:
        return _("The record is after the session was manually closed.")
    if record.status not in (
        ATTENDANCE_STATUS_PRESENT,
        ATTENDANCE_STATUS_LATE,
        ATTENDANCE_STATUS_EXCUSED,
    ):
        return _("The attendance status is not included in evaluation.")
    return None


def _build_attendance_summary(
    total_sessions,
    present_count,
    late_count,
    excused_count,
    raw_record_count=None,
) -> dict:
    """One canonical attendance summary projection (PERF-004).

    Shared by the single-enrollment and batch paths so the semantics are
    identical everywhere: for diploma periods the denominator counts only
    dated sessions whose
    attendance window has ended (close_at passed) or that are already closed
    (L4); scheduled and open future sessions must not lower attendance before
    they happen.
    """
    total_attended = present_count + late_count
    counted_record_count = present_count + late_count + excused_count
    raw_record_count = (
        counted_record_count if raw_record_count is None else raw_record_count
    )
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
        "raw_record_count": raw_record_count,
        "counted_record_count": counted_record_count,
        "excluded_record_count": max(raw_record_count - counted_record_count, 0),
    }


def _ended_or_closed_sessions(cohort_ids, semester, now):
    return AttendanceSession.objects.filter(
        cohort_id__in=cohort_ids,
        semester=semester,
        period__isnull=False,
    ).filter(
        Q(status=SESSION_STATUS_CLOSED) | Q(close_at__lte=now),
    ).exclude(status=SESSION_STATUS_CANCELLED)


def get_attendance_summary(diploma_enrollment, semester) -> dict:
    """Attendance summary for one enrollment/semester (single aggregate path)."""
    now = timezone.now()
    sessions = _ended_or_closed_sessions(
        [diploma_enrollment.cohort_id], semester, now,
    )

    counted_in_evaluation = counted_attendance_record_q() & Q(session__in=sessions)
    counts = AttendanceRecord.objects.filter(
        diploma_enrollment=diploma_enrollment,
        session__cohort_id=diploma_enrollment.cohort_id,
        session__semester=semester,
        session__period__isnull=False,
    ).aggregate(
        raw_record_count=Count("id"),
        present_count=Count(
            "id", filter=counted_in_evaluation & Q(status=ATTENDANCE_STATUS_PRESENT),
        ),
        late_count=Count(
            "id", filter=counted_in_evaluation & Q(status=ATTENDANCE_STATUS_LATE),
        ),
        excused_count=Count(
            "id", filter=counted_in_evaluation & Q(status=ATTENDANCE_STATUS_EXCUSED),
        ),
    )

    return _build_attendance_summary(
        sessions.count(),
        counts["present_count"],
        counts["late_count"],
        counts["excused_count"],
        raw_record_count=counts["raw_record_count"],
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

    counted_in_evaluation = counted_attendance_record_q() & Q(session__in=sessions)
    record_counts = {
        row["diploma_enrollment_id"]: row
        for row in AttendanceRecord.objects.filter(
            diploma_enrollment_id__in=[e.id for e in enrollments],
            session__cohort_id=F("diploma_enrollment__cohort_id"),
            session__semester=semester,
            session__period__isnull=False,
        ).values("diploma_enrollment_id").annotate(
            raw_record_count=Count("id"),
            present_count=Count(
                "id", filter=counted_in_evaluation & Q(status=ATTENDANCE_STATUS_PRESENT),
            ),
            late_count=Count(
                "id", filter=counted_in_evaluation & Q(status=ATTENDANCE_STATUS_LATE),
            ),
            excused_count=Count(
                "id", filter=counted_in_evaluation & Q(status=ATTENDANCE_STATUS_EXCUSED),
            ),
        )
    }

    summaries = {}
    for enrollment in enrollments:
        counts = record_counts.get(enrollment.id, {})
        summaries[enrollment.id] = _build_attendance_summary(
            session_totals.get(enrollment.cohort_id, 0),
            counts.get("present_count", 0),
            counts.get("late_count", 0),
            counts.get("excused_count", 0),
            raw_record_count=counts.get("raw_record_count", 0),
        )
    return summaries


def get_attendance_evaluation_percent(
    enrollment: DiplomaEnrollment,
    semester: Semester,
    required_sessions: int | None = None,
    summary: dict | None = None,
) -> Decimal | None:
    """Canonical attendance percent for one enrollment/semester (DECISION §13).

    With ``required_sessions`` > 0 the percent is attended dated sessions out
    of the required number (e.g. 7 of 10 ⇒ 70%), capped at 100 and quantized
    to 0.01. Otherwise it falls back to attended / ended-closed sessions (the
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
