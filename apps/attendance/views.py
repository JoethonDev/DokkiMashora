"""Views for attendance management."""

from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods, require_POST

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import Cohort, CourseVersion, Semester
from apps.academics.services import get_student_diploma_access
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

from .constants import (
    ATTENDANCE_STATUS_EXCUSED,
    ATTENDANCE_STATUS_LATE,
    ATTENDANCE_STATUS_PRESENT,
    SESSION_STATUS_OPEN,
    SESSION_STATUS_SCHEDULED,
)
from .models import AttendanceRecord, AttendanceSession
from .services import (
    close_attendance_session,
    get_attendance_summary,
    open_attendance_session,
    open_session,
    scan_attendance_token,
)

ADMIN_ONLY = (GROUP_SUPER_ADMIN, GROUP_ADMIN)


@require_http_methods(["GET"])
@login_required
@role_required(*ROLE_ATTENDANCE_STAFF)
def semester_options_by_cohort(request):
    cohort_id = request.GET.get("cohort_id")
    semesters = Semester.objects.none()
    if cohort_id:
        cohort = get_object_or_404(Cohort, id=cohort_id)
        semesters = Semester.objects.filter(diploma=cohort.diploma, is_active=True).order_by("order")
    return render(request, "ui/fragments/admin/_attendance_semester_options.html", {"semesters": semesters})


# ---------------------------------------------------------------------------
# Admin session list
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_ATTENDANCE_STAFF)
def attendance_session_list(request):
    sessions = AttendanceSession.objects.all().select_related(
        "cohort", "course_version", "created_by"
    ).annotate(record_count=Count("records"))

    cohort_id = safe_int(request.GET.get("cohort_id"))
    date_from = safe_date_str(request.GET.get("date_from"))
    date_to = safe_date_str(request.GET.get("date_to"))
    exception = request.GET.get("exception", "")

    if cohort_id:
        sessions = sessions.filter(cohort_id=cohort_id)
    if date_from:
        sessions = sessions.filter(date__gte=date_from)
    if date_to:
        sessions = sessions.filter(date__lte=date_to)
    if exception == "overdue":
        sessions = sessions.filter(status="scheduled", date__lt=timezone.localdate())

    sessions = sessions.order_by("-date", "-created_at")

    ctx = {
        "sessions": sessions,
        "cohorts": Cohort.objects.filter(is_active=True),
        "selected_cohort_id": cohort_id,
        "date_from": date_from,
        "date_to": date_to,
        "exception": exception,
        "breadcrumb_items": [{"label": _("Attendance Sessions")}],
        "is_admin": is_admin(request.user),
    }
    return render(request, "ui/pages/admin/attendance_list.html", ctx)


# ---------------------------------------------------------------------------
# Admin session create
# ---------------------------------------------------------------------------


@login_required
@role_required(*ADMIN_ONLY)
def attendance_session_create(request):
    if request.method == "POST":
        target_type = request.POST.get("target_type")
        cohort_id = safe_int(request.POST.get("cohort_id"))
        course_version_id = safe_int(request.POST.get("course_version_id"))
        semester_id = safe_int(request.POST.get("semester_id"))
        title = request.POST.get("title", "")
        open_at_str = request.POST.get("open_at", "")
        close_at_str = request.POST.get("close_at", "")

        if not open_at_str or not close_at_str:
            messages.error(request, _("Open and close times are required."))
            return redirect("attendance:attendance_session_create")

        try:
            open_at = datetime.fromisoformat(open_at_str)
            close_at = datetime.fromisoformat(close_at_str)
            if timezone.is_naive(open_at):
                open_at = timezone.make_aware(open_at)
            if timezone.is_naive(close_at):
                close_at = timezone.make_aware(close_at)
        except (ValueError, TypeError):
            messages.error(request, _("Invalid date/time format."))
            return redirect("attendance:attendance_session_create")

        if target_type not in {"diploma", "course"}:
            messages.error(request, _("Select a target type."))
            return redirect("attendance:attendance_session_create")
        if target_type == "diploma" and (not cohort_id or course_version_id):
            messages.error(request, _("Select a cohort and no course version for a diploma session."))
            return redirect("attendance:attendance_session_create")
        if target_type == "course" and (not course_version_id or cohort_id or semester_id):
            messages.error(request, _("Select a course version and no cohort or semester for a course session."))
            return redirect("attendance:attendance_session_create")

        if target_type == "diploma":
            target = get_object_or_404(Cohort, id=cohort_id)
        else:
            target = get_object_or_404(CourseVersion, id=course_version_id)

        semester = None
        if semester_id:
            semester = get_object_or_404(Semester, id=semester_id)
            if cohort_id and semester.diploma_id != target.diploma_id:
                messages.error(request, _("Semester does not belong to the selected cohort's diploma."))
                return redirect("attendance:attendance_session_create")

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
    course_versions = CourseVersion.objects.filter(is_active=True).select_related("course")
    semesters = Semester.objects.filter(is_active=True).order_by("diploma_id", "order")
    ctx = {
        "cohorts": cohorts,
        "course_versions": course_versions,
        "semesters": semesters,
        "breadcrumb_items": [
            {"label": _("Attendance Sessions"), "href": reverse("attendance:attendance_session_list")},
            {"label": _("Create Session")},
        ],
    }
    return render(request, "ui/pages/admin/attendance_form.html", ctx)


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
        "is_admin": is_admin(request.user),
        "breadcrumb_items": [
            {"label": _("Attendance Sessions"), "href": reverse("attendance:attendance_session_list")},
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


# ---------------------------------------------------------------------------
# Student my-attendance
# ---------------------------------------------------------------------------


@student_required
def my_attendance(request):
    student_profile = getattr(request.user, "student_profile", None)
    if not student_profile:
        messages.error(request, _("Student profile not found."))
        return redirect("core:home")

    enrollments = student_profile.diploma_enrollments.filter(
        status=ENROLLMENT_STATUS_ACTIVE,
    ).select_related("cohort__diploma")

    summaries = []
    for enrollment in enrollments:
        for semester in get_student_diploma_access(enrollment)["visible_semesters"]:
            summary = get_attendance_summary(enrollment, semester)
            if summary["total_sessions"] > 0:
                summary["enrollment"] = enrollment
                summary["semester"] = semester
                summaries.append(summary)

    course_enrollments = student_profile.course_enrollments.filter(
        status=ENROLLMENT_STATUS_ACTIVE,
    ).select_related("course_version__course")
    for ce in course_enrollments:
        sessions = AttendanceSession.objects.filter(
            course_version=ce.course_version,
        )
        total = sessions.count()
        records = AttendanceRecord.objects.filter(
            session__in=sessions, course_enrollment=ce,
        )
        present = records.filter(status=ATTENDANCE_STATUS_PRESENT).count()
        late = records.filter(status=ATTENDANCE_STATUS_LATE).count()
        if total > 0:
            summaries.append({
                "enrollment": ce,
                "course_title": str(ce.course_version.course.title),
                "total_sessions": total,
                "present_count": present,
                "late_count": late,
                "excused_count": records.filter(status=ATTENDANCE_STATUS_EXCUSED).count(),
                "total_attended": present + late,
                "attendance_percent": round(((present + late) / total) * 100, 2),
            })

    ctx = {
        "summaries": summaries,
        "breadcrumb_items": [{"label": _("My Attendance")}],
    }
    return render(request, "ui/pages/student/attendance.html", ctx)
