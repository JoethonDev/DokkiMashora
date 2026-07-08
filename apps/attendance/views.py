"""Views for attendance management."""

from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.academics.models import Cohort, CourseVersion, Semester
from apps.core.permissions import (
    GROUP_ADMIN,
    GROUP_SUPER_ADMIN,
    ROLE_ATTENDANCE_STAFF,
    role_required,
    safe_int,
)

from .constants import SESSION_STATUS_OPEN, SESSION_STATUS_SCHEDULED
from .models import AttendanceSession
from .services import (
    close_attendance_session,
    get_attendance_summary,
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
    sessions = AttendanceSession.objects.all().select_related(
        "cohort", "course_version", "created_by"
    )

    cohort_id = safe_int(request.GET.get("cohort_id"))
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")

    if cohort_id:
        sessions = sessions.filter(cohort_id=cohort_id)
    if date_from:
        sessions = sessions.filter(date__gte=date_from)
    if date_to:
        sessions = sessions.filter(date__lte=date_to)

    sessions = sessions.order_by("-date", "-created_at")

    ctx = {
        "sessions": sessions,
        "cohorts": Cohort.objects.filter(is_active=True),
        "selected_cohort_id": cohort_id,
        "date_from": date_from,
        "date_to": date_to,
        "breadcrumb_items": [{"label": _("Attendance Sessions")}],
    }
    return render(request, "attendance/session_list.html", ctx)


# ---------------------------------------------------------------------------
# Admin session create
# ---------------------------------------------------------------------------


@login_required
@role_required(*ADMIN_ONLY)
def attendance_session_create(request):
    if request.method == "POST":
        cohort_id = safe_int(request.POST.get("cohort_id"))
        course_version_id = safe_int(request.POST.get("course_version_id"))
        semester_id = safe_int(request.POST.get("semester_id"))
        title = request.POST.get("title", "")
        open_at_str = request.POST.get("open_at", "")
        close_at_str = request.POST.get("close_at", "")

        if not open_at_str or not close_at_str:
            messages.error(request, _("Open and close times are required."))
            return redirect("attendance:attendance_session_create")

        open_at = timezone.make_aware(datetime.fromisoformat(open_at_str))
        close_at = timezone.make_aware(datetime.fromisoformat(close_at_str))

        if cohort_id:
            target = get_object_or_404(Cohort, id=cohort_id)
        elif course_version_id:
            target = get_object_or_404(CourseVersion, id=course_version_id)
        else:
            messages.error(request, _("Select a cohort or course version."))
            return redirect("attendance:attendance_session_create")

        semester = None
        if semester_id:
            semester = get_object_or_404(Semester, id=semester_id)

        try:
            session = open_attendance_session(
                target,
                semester=semester,
                open_at=open_at,
                close_at=close_at,
                actor=request.user,
                title=title,
            )
            messages.success(request, _("Attendance session created."))
            return redirect("attendance:attendance_session_detail", session.id)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("attendance:attendance_session_create")

    cohorts = Cohort.objects.filter(is_active=True).select_related("diploma")
    ctx = {
        "cohorts": cohorts,
        "breadcrumb_items": [
            {"label": _("Attendance Sessions"), "href": reverse("attendance:attendance_session_list")},
            {"label": _("Create Session")},
        ],
    }
    return render(request, "attendance/session_form.html", ctx)


# ---------------------------------------------------------------------------
# Admin session detail
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_ATTENDANCE_STAFF)
def attendance_session_detail(request, session_id):
    session = get_object_or_404(
        AttendanceSession.objects.select_related("cohort", "course_version", "semester", "created_by", "opened_by", "closed_by"),
        id=session_id,
    )
    records = session.records.select_related("student", "scanned_by").order_by("scanned_at")

    ctx = {
        "session": session,
        "records": records,
        "is_admin": request.user.groups.filter(name__in=ADMIN_ONLY).exists(),
        "breadcrumb_items": [
            {"label": _("Attendance Sessions"), "href": reverse("attendance:attendance_session_list")},
            {"label": str(session)},
        ],
    }
    return render(request, "attendance/session_detail.html", ctx)


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
        "breadcrumb_items": [
            {"label": _("Attendance Sessions"), "href": reverse("attendance:attendance_session_list")},
            {"label": _("Records: %(session)s") % {"session": session}},
        ],
    }
    return render(request, "attendance/session_records.html", ctx)


# ---------------------------------------------------------------------------
# Secretary scan
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_ATTENDANCE_STAFF)
def attendance_scan(request):
    open_sessions = AttendanceSession.objects.filter(
        status=SESSION_STATUS_OPEN,
    ).select_related("cohort", "course_version").order_by("-date")

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

        # Parse payload: "attendance:{display_code}:{raw_token}"
        parts = payload.split(":")
        if len(parts) != 3 or parts[0] != "attendance":
            messages.error(request, _("Invalid QR payload format."))
            return redirect("attendance:attendance_scan")

        raw_token = parts[2]

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
    return render(request, "attendance/scan.html", ctx)


# ---------------------------------------------------------------------------
# Student my-attendance
# ---------------------------------------------------------------------------


@login_required
def my_attendance(request):
    student_profile = getattr(request.user, "student_profile", None)
    if not student_profile:
        messages.error(request, _("Student profile not found."))
        return redirect("core:home")

    enrollments = student_profile.diploma_enrollments.filter(
        status="active",
    ).select_related("cohort__diploma")

    summaries = []
    for enrollment in enrollments:
        semesters = Semester.objects.filter(
            diploma=enrollment.cohort.diploma,
        ).order_by("order")
        for semester in semesters:
            summary = get_attendance_summary(enrollment, semester)
            if summary["total_sessions"] > 0:
                summary["enrollment"] = enrollment
                summary["semester"] = semester
                summaries.append(summary)

    ctx = {
        "summaries": summaries,
        "breadcrumb_items": [{"label": _("My Attendance")}],
    }
    return render(request, "attendance/my_attendance.html", ctx)
