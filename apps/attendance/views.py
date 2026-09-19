"""Views for attendance management."""

from datetime import date as date_type
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import Cohort, CourseVersion, Diploma, DiplomaEnrollment, Semester
from apps.academics.services import get_student_diploma_access
from apps.audit.constants import ACTION_ATTENDANCE_SESSION_DELETED
from apps.audit.services import record_audit_event
from apps.core.htmx import is_htmx
from apps.core.pagination import paginate
from apps.core.permissions import (
    GROUP_ADMIN,
    GROUP_SUPER_ADMIN,
    ROLE_ATTENDANCE_STAFF,
    is_admin,
    role_required,
    safe_date_str,
    safe_int,
    student_required,
)
from apps.progression.models import SemesterEvaluationConfig

from .constants import (
    ATTENDANCE_STATUS_EXCUSED,
    ATTENDANCE_STATUS_LATE,
    ATTENDANCE_STATUS_PRESENT,
    SESSION_STATUS_CANCELLED,
    SESSION_STATUS_CLOSED,
    SESSION_STATUS_OPEN,
    SESSION_STATUS_SCHEDULED,
)
from .models import AttendancePeriod, AttendanceRecord, AttendanceSession
from .services import (
    AttendanceScanError,
    _build_attendance_summary,
    close_attendance_period,
    close_attendance_session,
    create_attendance_day,
    create_attendance_period,
    get_attendance_evaluation_percent,
    get_attendance_summaries,
    open_attendance_session,
    open_session,
    scan_attendance_token,
)

ADMIN_ONLY = (GROUP_SUPER_ADMIN, GROUP_ADMIN)


# ---------------------------------------------------------------------------
# Admin session list
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_ATTENDANCE_STAFF)
def attendance_session_list(request):
    periods = AttendancePeriod.objects.select_related(
        "cohort", "semester", "created_by"
    ).annotate(
        day_count=Count("sessions", distinct=True),
        record_count=Count("sessions__records", distinct=True),
    )
    sessions = AttendanceSession.objects.filter(period__isnull=True).select_related(
        "cohort", "course_version", "semester", "created_by"
    ).annotate(record_count=Count("records"))

    cohort_id = safe_int(request.GET.get("cohort_id"))
    date_from = safe_date_str(request.GET.get("date_from"))
    date_to = safe_date_str(request.GET.get("date_to"))
    exception = request.GET.get("exception", "")

    if cohort_id:
        periods = periods.filter(cohort_id=cohort_id)
        sessions = sessions.filter(cohort_id=cohort_id)
    if date_from:
        periods = periods.filter(sessions__date__gte=date_from).distinct()
        sessions = sessions.filter(date__gte=date_from)
    if date_to:
        periods = periods.filter(sessions__date__lte=date_to).distinct()
        sessions = sessions.filter(date__lte=date_to)
    if exception == "overdue":
        periods = periods.filter(status="scheduled", ends_at__lt=timezone.now())
        sessions = sessions.filter(status="scheduled", date__lt=timezone.localdate())

    periods = periods.order_by("-starts_at", "-created_at")
    page_obj, pagination_qs = paginate(request, periods, per_page=25)
    sessions = sessions.order_by("-date", "-created_at")

    ctx = {
        "periods": page_obj,
        "sessions": sessions,
        "page_obj": page_obj,
        "pagination_qs": pagination_qs,
        "cohorts": Cohort.objects.filter(is_active=True),
        "selected_cohort_id": cohort_id,
        "date_from": date_from,
        "date_to": date_to,
        "exception": exception,
        "breadcrumb_items": [{"label": _("Attendance periods")}],
        "is_admin": is_admin(request.user),
    }
    if is_htmx(request):
        return render(request, "ui/fragments/admin/_attendance_records.html", ctx)
    return render(request, "ui/pages/admin/attendance_list.html", ctx)


# ---------------------------------------------------------------------------
# Admin session create
# ---------------------------------------------------------------------------


@login_required
@role_required(*ADMIN_ONLY)
def attendance_session_create(request):
    """Create a diploma attendance period or a standalone course session."""
    form_errors = []
    form_data = {}
    if request.method == "POST":
        target_type = request.POST.get("target_type")
        cohort_id = safe_int(request.POST.get("cohort_id"))
        course_version_id = safe_int(request.POST.get("course_version_id"))
        semester_id = safe_int(request.POST.get("semester_id"))
        title = request.POST.get("title", "")
        open_at_str = request.POST.get("open_at", "")
        close_at_str = request.POST.get("close_at", "")
        form_data = {
            "target_type": target_type,
            "diploma_id": request.POST.get("diploma_id", ""),
            "cohort_id": request.POST.get("cohort_id", ""),
            "course_version_id": request.POST.get("course_version_id", ""),
            "semester_id": request.POST.get("semester_id", ""),
            "title": title,
            "open_at": open_at_str,
            "close_at": close_at_str,
        }

        open_at = None
        close_at = None
        if not open_at_str or not close_at_str:
            form_errors.append(_("Open and close times are required."))
        else:
            try:
                open_at = datetime.fromisoformat(open_at_str)
                close_at = datetime.fromisoformat(close_at_str)
                if timezone.is_naive(open_at):
                    open_at = timezone.make_aware(open_at)
                if timezone.is_naive(close_at):
                    close_at = timezone.make_aware(close_at)
            except (ValueError, TypeError):
                form_errors.append(_("Invalid date/time format."))

        if target_type not in {"diploma", "course"}:
            form_errors.append(_("Select a target type."))
        elif target_type == "diploma":
            if not cohort_id or course_version_id or not semester_id:
                form_errors.append(
                    _("Select a cohort and semester, and no course version for a diploma period.")
                )
        else:
            if not course_version_id or cohort_id or semester_id:
                form_errors.append(
                    _("Select a course version and no cohort or semester for a course session.")
                )

        if not form_errors:
            semester = None
            if semester_id:
                semester = get_object_or_404(Semester, id=semester_id)
            if target_type == "diploma":
                target = get_object_or_404(Cohort, id=cohort_id)
                if semester and semester.diploma_id != target.diploma_id:
                    form_errors.append(_("Semester does not belong to the selected cohort's diploma."))
            else:
                target = get_object_or_404(CourseVersion, id=course_version_id)

            if not form_errors:
                try:
                    if target_type == "diploma":
                        period = create_attendance_period(
                            target,
                            semester,
                            starts_at=open_at,
                            ends_at=close_at,
                            actor=request.user,
                            title=title,
                        )
                        messages.success(request, _("Attendance period created."))
                        return redirect("attendance:attendance_period_detail", period.id)
                    session = open_attendance_session(
                        target,
                        open_at=open_at,
                        close_at=close_at,
                        actor=request.user,
                        title=title,
                    )
                    messages.success(request, _("Attendance session created."))
                    return redirect("attendance:attendance_session_detail", session.id)
                except ValueError as exc:
                    form_errors.append(str(exc))

    cohorts = (
        Cohort.objects.filter(is_active=True)
        .select_related("diploma")
        .order_by("diploma__title", "name")
    )
    course_versions = (
        CourseVersion.objects.filter(is_active=True)
        .select_related("course")
        .order_by("course__title")
    )
    semesters = (
        Semester.objects.filter(is_active=True)
        .select_related("diploma")
        .order_by("diploma_id", "order")
    )
    ctx = {
        "cohorts": cohorts,
        "course_versions": course_versions,
        "semesters": semesters,
        "diplomas": Diploma.objects.filter(is_active=True).order_by("title"),
        "form_data": form_data,
        "form_errors": form_errors,
        "back_url": reverse("attendance:attendance_session_list"),
        "breadcrumb_items": [
            {"label": _("Attendance periods"), "href": reverse("attendance:attendance_session_list")},
            {"label": _("Create attendance")},
        ],
    }
    return render(request, "ui/pages/admin/attendance_form.html", ctx)


# ---------------------------------------------------------------------------
# Admin attendance period and dated-session detail
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_ATTENDANCE_STAFF)
def attendance_period_detail(request, period_id):
    period = get_object_or_404(
        AttendancePeriod.objects.select_related(
            "cohort", "semester", "created_by", "opened_by", "closed_by"
        ),
        id=period_id,
    )
    days = period.sessions.select_related("created_by", "opened_by", "closed_by").annotate(
        record_count=Count("records")
    ).order_by("session_number", "date")
    return render(request, "ui/pages/admin/attendance_period_detail.html", {
        "period": period,
        "days": days,
        "is_admin": is_admin(request.user),
        "back_url": reverse("attendance:attendance_session_list"),
        "breadcrumb_items": [
            {"label": _("Attendance periods"), "href": reverse("attendance:attendance_session_list")},
            {"label": str(period)},
        ],
    })


@login_required
@role_required(*ADMIN_ONLY)
@require_POST
def attendance_period_day_create(request, period_id):
    period = get_object_or_404(AttendancePeriod, id=period_id)
    session_date_raw = request.POST.get("session_date", "").strip()
    open_at_raw = request.POST.get("open_at", "").strip()
    close_at_raw = request.POST.get("close_at", "").strip()
    title = request.POST.get("title", "").strip()
    try:
        session_date = date_type.fromisoformat(session_date_raw)
        open_at = datetime.fromisoformat(open_at_raw)
        close_at = datetime.fromisoformat(close_at_raw)
        if timezone.is_naive(open_at):
            open_at = timezone.make_aware(open_at)
        if timezone.is_naive(close_at):
            close_at = timezone.make_aware(close_at)
        create_attendance_day(
            period,
            session_date=session_date,
            open_at=open_at,
            close_at=close_at,
            actor=request.user,
            title=title,
        )
        messages.success(request, _("Attendance session added to the period."))
    except (TypeError, ValueError) as exc:
        messages.error(request, str(exc) or _("Enter a valid session date and time."))
    return redirect("attendance:attendance_period_detail", period.id)


@login_required
@role_required(*ADMIN_ONLY)
@require_POST
def attendance_period_close(request, period_id):
    period = get_object_or_404(AttendancePeriod, id=period_id)
    try:
        close_attendance_period(period, request.user)
        messages.success(request, _("Attendance period closed."))
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("attendance:attendance_period_detail", period.id)


@login_required
@role_required(*ADMIN_ONLY)
@require_POST
def attendance_session_delete(request, session_id):
    """Delete an attendance session that has no recorded attendance history.

    Sessions with records are preserved academic evidence and cannot be
    deleted; the deletion of an empty session is audited.
    """
    session = get_object_or_404(
        AttendanceSession.objects.select_related("cohort", "course_version", "semester", "period"),
        id=session_id,
    )
    if AttendanceRecord.objects.filter(session=session).exists():
        messages.error(
            request,
            _("This session has recorded attendance and cannot be deleted."),
        )
        return redirect(
            "attendance:attendance_period_detail", session.period_id
        ) if session.period_id else redirect("attendance:attendance_session_detail", session.id)
    info = str(session)
    record_audit_event(
        actor=request.user,
        action=ACTION_ATTENDANCE_SESSION_DELETED,
        target_type="attendance_session",
        target_id=str(session.pk),
        metadata={
            "title": session.title,
            "date": str(session.date),
            "cohort_id": session.cohort_id,
            "course_version_id": session.course_version_id,
            "semester_id": session.semester_id,
            "status": session.status,
        },
    )
    session.delete()
    messages.success(request, _("Attendance session deleted: %(session)s.") % {"session": info})
    return redirect(
        "attendance:attendance_period_detail", session.period_id
    ) if session.period_id else redirect("attendance:attendance_session_list")


@login_required
@role_required(*ROLE_ATTENDANCE_STAFF)
def attendance_session_detail(request, session_id):
    session = get_object_or_404(
        AttendanceSession.objects.select_related("cohort", "course_version", "semester", "period", "created_by", "opened_by", "closed_by"),
        id=session_id,
    )
    records = session.records.select_related("student", "scanned_by").order_by("scanned_at")

    ctx = {
        "session": session,
        "records": records,
        "period": session.period,
        "is_admin": is_admin(request.user),
        "back_url": reverse(
            "attendance:attendance_period_detail", args=[session.period_id]
        ) if session.period_id else reverse("attendance:attendance_session_list"),
        "breadcrumb_items": [
            {"label": _("Attendance periods"), "href": reverse("attendance:attendance_session_list")},
            {"label": str(session)},
        ],
    }
    return render(request, "ui/pages/admin/attendance_detail.html", ctx)


# ---------------------------------------------------------------------------
# Admin session open
# ---------------------------------------------------------------------------


@login_required
@role_required(*ADMIN_ONLY)
@require_POST
def attendance_session_open(request, session_id):
    session = get_object_or_404(AttendanceSession, id=session_id)

    if session.status != SESSION_STATUS_SCHEDULED:
        messages.error(request, _("Only a scheduled session can be opened."))
        return redirect("attendance:attendance_session_detail", session.id)

    try:
        open_session(session, request.user)
        messages.success(request, _("Session opened."))
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("attendance:attendance_session_detail", session.id)


# ---------------------------------------------------------------------------
# Admin session close
# ---------------------------------------------------------------------------


@login_required
@role_required(*ADMIN_ONLY)
@require_POST
def attendance_session_close(request, session_id):
    session = get_object_or_404(AttendanceSession, id=session_id)

    try:
        close_attendance_session(session, request.user)
        messages.success(request, _("Session closed."))
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("attendance:attendance_session_detail", session.id)


# ---------------------------------------------------------------------------
# Admin session records
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_ATTENDANCE_STAFF)
def attendance_session_records(request, session_id):
    session = get_object_or_404(AttendanceSession, id=session_id)
    records = session.records.select_related("student", "scanned_by", "attendance_code").order_by("scanned_at")

    ctx = {
        "session": session,
        "records": records,
        "period": session.period,
        "back_url": reverse(
            "attendance:attendance_period_detail", args=[session.period_id]
        ) if session.period_id else reverse("attendance:attendance_session_list"),
        "breadcrumb_items": [
            {"label": _("Attendance Sessions"), "href": reverse("attendance:attendance_session_list")},
            {"label": _("Records: %(session)s") % {"session": session}},
        ],
    }
    return render(request, "ui/pages/admin/attendance_detail.html", ctx)


# ---------------------------------------------------------------------------
# Secretary scan
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_ATTENDANCE_STAFF)
def attendance_scan(request):
    open_sessions = AttendanceSession.objects.filter(
        status=SESSION_STATUS_OPEN,
    ).select_related("cohort", "course_version", "semester", "period").order_by("-date", "-session_number")

    result = None

    if request.method == "POST":
        session_id = safe_int(request.POST.get("session_id"))
        payload = request.POST.get("qr_payload", "").strip()

        if not session_id:
            messages.error(request, _("Select a session."))
            return redirect("attendance:attendance_scan")

        if not payload:
            messages.error(request, _("Enter a QR payload."))
            return redirect("attendance:attendance_scan")

        session = get_object_or_404(AttendanceSession, id=session_id)

        # Parse payload: "attendance:{signed_pk}" (signed_pk is TimestampSigner "pk:timestamp:signature")
        parts = payload.split(":")
        if len(parts) < 3 or parts[0] != "attendance":
            messages.error(request, _("Invalid QR payload format."))
            return redirect("attendance:attendance_scan")

        raw_token = ":".join(parts[1:])

        try:
            record, already_exists = scan_attendance_token(
                raw_token,
                session=session,
                scanned_by=request.user,
            )
            if already_exists:
                result = {
                    "status": "duplicate",
                    "student_name": str(record.student),
                }
                messages.warning(request, _("Attendance already recorded for %(student)s.") % {"student": record.student})
            else:
                result = {
                    "status": "success",
                    "student_name": str(record.student),
                }
                messages.success(request, _("Attendance recorded for %(student)s.") % {"student": record.student})
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("attendance:attendance_scan")

    ctx = {
        "open_sessions": open_sessions,
        "result": result,
        "breadcrumb_items": [{"label": _("Scan Attendance")}],
    }
    return render(request, "ui/pages/admin/attendance_scan.html", ctx)


@require_POST
@login_required
@role_required(*ROLE_ATTENDANCE_STAFF)
def attendance_scan_api(request):
    """Structured JSON endpoint for the browser-camera scanner (P7-01).

    Uses the exact same authoritative service as the manual form; returns a
    stable ``code`` for every outcome so the scanner can render states without
    parsing prose. Never trusts client state — session/student/cohort/window/
    replay are all validated server-side.
    """
    session_id = safe_int(request.POST.get("session_id"))
    payload = request.POST.get("qr_payload", "").strip()

    if not session_id:
        return JsonResponse({"code": "invalid_request", "message": str(_("Select a session."))}, status=400)
    session = AttendanceSession.objects.filter(id=session_id).first()
    if session is None:
        return JsonResponse({"code": "invalid_request", "message": str(_("Session not found."))}, status=404)

    parts = payload.split(":")
    if len(parts) < 3 or parts[0] != "attendance":
        return JsonResponse({"code": "invalid_token", "message": str(_("Invalid QR payload format."))}, status=400)
    raw_token = ":".join(parts[1:])

    try:
        record, already_exists = scan_attendance_token(
            raw_token,
            session=session,
            scanned_by=request.user,
        )
    except AttendanceScanError as exc:
        return JsonResponse({"code": exc.code, "message": str(exc)}, status=422)
    except ValueError as exc:
        return JsonResponse({"code": "invalid_token", "message": str(exc)}, status=422)

    if already_exists:
        return JsonResponse({
            "code": "duplicate",
            "message": str(_("Attendance already recorded for %(student)s.") % {"student": record.student}),
            "student_name": str(record.student),
        }, status=200)
    return JsonResponse({
        "code": "recorded",
        "message": str(_("Attendance recorded for %(student)s.") % {"student": record.student}),
        "student_name": str(record.student),
    }, status=201)


# ---------------------------------------------------------------------------
# Student my-attendance
# ---------------------------------------------------------------------------


@student_required
def my_attendance(request):
    student_profile = getattr(request.user, "student_profile", None)
    if not student_profile:
        messages.error(request, _("Student profile not found."))
        return redirect("core:home")

    now = timezone.now()
    ended_or_closed = (Q(status=SESSION_STATUS_CLOSED) | Q(close_at__lte=now)) & ~Q(
        status=SESSION_STATUS_CANCELLED
    )

    summaries = []

    enrollments = student_profile.diploma_enrollments.filter(
        status=ENROLLMENT_STATUS_ACTIVE,
    ).select_related("cohort__diploma")

    # Reuse the canonical batch attendance summaries (one set-based pass per
    # semester) and the same required-sessions capping the evaluation uses, so
    # every diploma card shows exactly the percentage the evaluation uses
    # (DECISION §13 — one attendance calculation everywhere).
    pairs_by_semester: dict[int, list[DiplomaEnrollment]] = {}
    semester_by_id: dict[int, Semester] = {}
    cohort_ids: set[int] = set()
    for enrollment in enrollments:
        visible_semesters = get_student_diploma_access(enrollment)["visible_semesters"]
        for semester in visible_semesters:
            pairs_by_semester.setdefault(semester.id, []).append(enrollment)
            semester_by_id[semester.id] = semester
            cohort_ids.add(enrollment.cohort_id)

    required_by_key: dict[tuple[int, int], int] = {}
    if semester_by_id:
        required_by_key = {
            (config.cohort_id, config.semester_id): config.attendance_required_sessions
            for config in SemesterEvaluationConfig.objects.filter(
                cohort_id__in=cohort_ids,
                semester_id__in=list(semester_by_id),
            )
        }

    for semester_id, enrollment_group in pairs_by_semester.items():
        semester = semester_by_id[semester_id]
        summaries_by_enrollment = get_attendance_summaries(enrollment_group, semester)
        for enrollment in enrollment_group:
            summary = summaries_by_enrollment.get(enrollment.id)
            if summary is None or not summary["total_sessions"]:
                continue
            required = required_by_key.get((enrollment.cohort_id, semester_id))
            summaries.append({
                "enrollment": enrollment,
                "semester": semester,
                **summary,
                "attendance_percent": get_attendance_evaluation_percent(
                    enrollment, semester, required, summary=summary,
                ),
            })

    course_enrollments = student_profile.course_enrollments.filter(
        status=ENROLLMENT_STATUS_ACTIVE,
    ).select_related("course_version__course")
    for ce in course_enrollments:
        session_qs = AttendanceSession.objects.filter(
            course_version=ce.course_version,
            period__isnull=True,
        ).filter(ended_or_closed)
        total = session_qs.count()
        if total == 0:
            continue
        record_counts = {}
        for row in AttendanceRecord.objects.filter(
            course_enrollment=ce,
            session__in=session_qs,
        ).values("status").annotate(count=Count("id")):
            record_counts[row["status"]] = row["count"]
        summary = _build_attendance_summary(
            total,
            record_counts.get(ATTENDANCE_STATUS_PRESENT, 0),
            record_counts.get(ATTENDANCE_STATUS_LATE, 0),
            record_counts.get(ATTENDANCE_STATUS_EXCUSED, 0),
        )
        summaries.append({
            "enrollment": ce,
            "course_title": str(ce.course_version.course.title),
            **summary,
        })

    ctx = {
        "summaries": summaries,
        "breadcrumb_items": [{"label": _("My Attendance")}],
    }
    return render(request, "ui/pages/student/attendance.html", ctx)
