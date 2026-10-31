"""Views for attendance management."""

from datetime import date as date_type
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, F, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods, require_POST

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import Cohort, CourseVersion, Diploma, DiplomaEnrollment, Semester
from apps.academics.services import get_visible_semesters_by_enrollment
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
from .forms import AttendancePeriodWindowForm, AttendanceSessionWindowForm
from .models import AttendancePeriod, AttendanceRecord, AttendanceSession
from .selectors import (
    annotate_attendance_history_records,
    effective_overdue_period_q,
    effective_overdue_session_q,
    student_attendance_history_queryset,
)
from .services import (
    AttendanceScanError,
    _build_attendance_summary,
    attendance_record_exclusion_reason,
    close_attendance_period,
    close_attendance_session,
    counted_attendance_record_q,
    create_attendance_day,
    create_attendance_period,
    delete_empty_attendance_session,
    get_attendance_evaluation_percent,
    get_attendance_summaries,
    open_attendance_session,
    open_session,
    scan_attendance_token,
    update_attendance_period_window,
    update_attendance_session_window,
)

ADMIN_ONLY = (GROUP_SUPER_ADMIN, GROUP_ADMIN)


# ---------------------------------------------------------------------------
# Admin session list
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_ATTENDANCE_STAFF)
def attendance_session_list(request):
    periods = AttendancePeriod.objects.select_related(
        "cohort__diploma", "semester__diploma", "created_by"
    ).annotate(
        day_count=Count("sessions", distinct=True),
        raw_record_count=Count("sessions__records", distinct=True),
        counted_record_count=Count(
            "sessions__records",
            filter=counted_attendance_record_q("sessions__records__"),
            distinct=True,
        ),
    ).annotate(
        excluded_record_count=F("raw_record_count") - F("counted_record_count"),
    )
    sessions = AttendanceSession.objects.filter(period__isnull=True).select_related(
        "cohort__diploma", "course_version__course", "semester__diploma", "created_by"
    ).annotate(
        raw_record_count=Count("records"),
        counted_record_count=Count(
            "records", filter=counted_attendance_record_q("records__"),
        ),
    ).annotate(
        excluded_record_count=F("raw_record_count") - F("counted_record_count"),
    )

    def query_id(name):
        raw_value = request.GET.get(name)
        if not raw_value:
            return None
        value = safe_int(raw_value)
        if value is None or value < 1:
            raise Http404
        return value

    diploma_id = query_id("diploma_id")
    cohort_id = query_id("cohort_id")
    semester_id = query_id("semester_id")
    selected_diploma = get_object_or_404(Diploma, id=diploma_id) if diploma_id else None
    selected_cohort = (
        get_object_or_404(Cohort.objects.select_related("diploma"), id=cohort_id)
        if cohort_id else None
    )
    selected_semester = (
        get_object_or_404(Semester.objects.select_related("diploma"), id=semester_id)
        if semester_id else None
    )

    inferred_diploma_id = (
        selected_cohort.diploma_id if selected_cohort
        else selected_semester.diploma_id if selected_semester
        else None
    )
    if selected_diploma is None and inferred_diploma_id:
        selected_diploma = get_object_or_404(Diploma, id=inferred_diploma_id)
    # These are independent list filters, not an object-access boundary. If a
    # user changes a parent selector while a stale child value remains in the
    # query string, discard the incompatible child instead of returning a 404.
    if selected_diploma and selected_cohort and selected_cohort.diploma_id != selected_diploma.id:
        selected_cohort = None
    if selected_diploma and selected_semester and selected_semester.diploma_id != selected_diploma.id:
        selected_semester = None
    if selected_cohort and selected_semester and selected_cohort.diploma_id != selected_semester.diploma_id:
        selected_semester = None

    date_from = safe_date_str(request.GET.get("date_from"))
    date_to = safe_date_str(request.GET.get("date_to"))
    exception = request.GET.get("exception", "")

    if selected_diploma:
        periods = periods.filter(cohort__diploma=selected_diploma)
        sessions = sessions.filter(
            Q(cohort__diploma=selected_diploma) | Q(semester__diploma=selected_diploma)
        )
    if selected_cohort:
        periods = periods.filter(cohort=selected_cohort)
        sessions = sessions.filter(cohort=selected_cohort)
    if selected_semester:
        periods = periods.filter(semester=selected_semester)
        sessions = sessions.filter(semester=selected_semester)
    if date_from:
        periods = periods.filter(sessions__date__gte=date_from).distinct()
        sessions = sessions.filter(date__gte=date_from)
    if date_to:
        periods = periods.filter(sessions__date__lte=date_to).distinct()
        sessions = sessions.filter(date__lte=date_to)
    if exception == "overdue":
        now = timezone.now()
        periods = periods.filter(effective_overdue_period_q(now))
        sessions = sessions.filter(effective_overdue_session_q(now))

    periods = periods.order_by("-starts_at", "-created_at")
    page_obj, pagination_qs = paginate(request, periods, per_page=25)
    sessions = sessions.order_by("-date", "-created_at")

    diplomas = Diploma.objects.filter(is_active=True)
    if selected_diploma and not selected_diploma.is_active:
        diplomas |= Diploma.objects.filter(id=selected_diploma.id)
    cohorts = Cohort.objects.filter(is_active=True)
    semesters = Semester.objects.filter(is_active=True)
    if selected_diploma:
        cohorts = cohorts.filter(diploma=selected_diploma)
        semesters = semesters.filter(diploma=selected_diploma)
    if selected_cohort and not selected_cohort.is_active:
        cohorts |= Cohort.objects.filter(id=selected_cohort.id)
    if selected_semester and not selected_semester.is_active:
        semesters |= Semester.objects.filter(id=selected_semester.id)

    course_sessions = sessions.filter(course_version__isnull=False)
    legacy_diploma_sessions = sessions.filter(cohort__isnull=False)

    ctx = {
        "periods": page_obj,
        "period_count": page_obj.paginator.count,
        "course_sessions": course_sessions,
        "legacy_diploma_sessions": legacy_diploma_sessions,
        "page_obj": page_obj,
        "pagination_qs": pagination_qs,
        "diplomas": diplomas.order_by("title"),
        "cohorts": cohorts.select_related("diploma").order_by(
            "diploma__title", "name",
        ),
        "semesters": semesters.select_related("diploma").order_by(
            "diploma__title", "order",
        ),
        "selected_diploma_id": selected_diploma.id if selected_diploma else None,
        "selected_cohort_id": selected_cohort.id if selected_cohort else None,
        "selected_semester_id": selected_semester.id if selected_semester else None,
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
        Cohort.objects.filter(is_active=True, diploma__attendance_enabled=True)
        .select_related("diploma")
        .order_by("diploma__title", "name")
    )
    course_versions = (
        CourseVersion.objects.filter(is_active=True, course__attendance_enabled=True)
        .select_related("course")
        .order_by("course__title")
    )
    semesters = (
        Semester.objects.filter(is_active=True, diploma__attendance_enabled=True)
        .select_related("diploma")
        .order_by("diploma_id", "order")
    )
    ctx = {
        "cohorts": cohorts,
        "course_versions": course_versions,
        "semesters": semesters,
        "diplomas": Diploma.objects.filter(
            is_active=True, attendance_enabled=True
        ).order_by("title"),
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
        ).annotate(
            raw_record_count=Count("sessions__records", distinct=True),
            counted_record_count=Count(
                "sessions__records",
                filter=counted_attendance_record_q("sessions__records__"),
                distinct=True,
            ),
        ).annotate(
            excluded_record_count=F("raw_record_count") - F("counted_record_count"),
        ),
        id=period_id,
    )
    days = period.sessions.select_related(
        "created_by", "opened_by", "closed_by", "period",
    ).annotate(
        raw_record_count=Count("records"),
        counted_record_count=Count(
            "records", filter=counted_attendance_record_q("records__"),
        ),
    ).annotate(
        excluded_record_count=F("raw_record_count") - F("counted_record_count"),
    ).order_by("session_number", "date")
    period_start_date = timezone.localtime(period.starts_at).date()
    period_end_date = timezone.localtime(period.ends_at).date()
    for day in days:
        day.outside_parent_period_window = (
            day.date < period_start_date
            or day.date > period_end_date
            or day.open_at < period.starts_at
            or day.close_at > period.ends_at
        )
    return render(request, "ui/pages/admin/attendance_period_detail.html", {
        "period": period,
        "days": days,
        "is_admin": is_admin(request.user),
        "back_url": reverse("attendance:attendance_session_list"),
        "breadcrumb_items": [
            {"label": _("Attendance periods"), "href": reverse("attendance:attendance_session_list")},
            {"label": _("Period details")},
        ],
    })


@login_required
@role_required(*ADMIN_ONLY)
@require_http_methods(["GET", "POST"])
def attendance_period_edit(request, period_id):
    period = get_object_or_404(
        AttendancePeriod.objects.select_related("cohort__diploma", "semester__diploma"),
        id=period_id,
    )
    initial = {"starts_at": period.starts_at, "ends_at": period.ends_at}
    form = AttendancePeriodWindowForm(
        request.POST if request.method == "POST" else None,
        initial=initial if request.method == "GET" else None,
    )
    if request.method == "POST" and form.is_valid():
        try:
            update_attendance_period_window(
                period,
                starts_at=form.cleaned_data["starts_at"],
                ends_at=form.cleaned_data["ends_at"],
                actor=request.user,
            )
        except ValueError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, _("Attendance period window updated."))
            return redirect("attendance:attendance_period_detail", period.id)

    return render(request, "ui/pages/admin/attendance_window_form.html", {
        "form": form,
        "page_title": _("Edit attendance period"),
        "object_title": str(period),
        "notice": _("Changing the period window never deletes attendance records."),
        "submit_label": _("Save changes"),
        "back_url": reverse("attendance:attendance_period_detail", args=[period.id]),
        "breadcrumb_items": [
            {"label": _("Attendance periods"), "href": reverse("attendance:attendance_session_list")},
            {"label": _("Period details"), "href": reverse("attendance:attendance_period_detail", args=[period.id])},
            {"label": _("Edit attendance period")},
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
    try:
        info, period_id = delete_empty_attendance_session(session, request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(
            "attendance:attendance_period_detail", session.period_id
        ) if session.period_id else redirect("attendance:attendance_session_detail", session.id)

    messages.success(request, _("Attendance session deleted: %(session)s.") % {"session": info})
    return redirect(
        "attendance:attendance_period_detail", period_id
    ) if period_id else redirect("attendance:attendance_session_list")


def _attendance_session_breadcrumbs(
    session: AttendanceSession,
    *,
    include_session_link: bool = False,
) -> list[dict[str, object]]:
    breadcrumbs = [{
        "label": _("Attendance periods") if session.period_id else _("Attendance sessions"),
        "href": reverse("attendance:attendance_session_list"),
    }]
    if session.period_id:
        breadcrumbs.append({
            "label": _("Period details"),
            "href": reverse("attendance:attendance_period_detail", args=[session.period_id]),
        })
        session_label = f"{_('Session')} {session.session_number}"
    else:
        session_label = _("Attendance session")
    session_breadcrumb = {"label": session_label}
    if include_session_link:
        session_breadcrumb["href"] = reverse(
            "attendance:attendance_session_detail", args=[session.id],
        )
    breadcrumbs.append(session_breadcrumb)
    return breadcrumbs


@login_required
@role_required(*ROLE_ATTENDANCE_STAFF)
def attendance_session_detail(request, session_id):
    session = get_object_or_404(
        AttendanceSession.objects.select_related("cohort", "course_version", "semester", "period", "created_by", "opened_by", "closed_by"),
        id=session_id,
    )
    record_projection = _attendance_record_projection(session)

    ctx = {
        "session": session,
        **record_projection,
        "period": session.period,
        "is_admin": is_admin(request.user),
        "back_url": reverse(
            "attendance:attendance_period_detail", args=[session.period_id]
        ) if session.period_id else reverse("attendance:attendance_session_list"),
        "breadcrumb_items": _attendance_session_breadcrumbs(session),
    }
    return render(request, "ui/pages/admin/attendance_detail.html", ctx)


@login_required
@role_required(*ADMIN_ONLY)
@require_http_methods(["GET", "POST"])
def attendance_session_edit(request, session_id):
    session = get_object_or_404(
        AttendanceSession.objects.select_related(
            "cohort__diploma", "course_version__course", "semester__diploma", "period",
        ),
        id=session_id,
    )
    initial = {
        "session_date": session.date,
        "open_at": session.open_at,
        "close_at": session.close_at,
        "title": session.title,
    }
    form = AttendanceSessionWindowForm(
        request.POST if request.method == "POST" else None,
        initial=initial if request.method == "GET" else None,
    )
    if request.method == "POST" and form.is_valid():
        try:
            update_attendance_session_window(
                session,
                session_date=form.cleaned_data["session_date"],
                open_at=form.cleaned_data["open_at"],
                close_at=form.cleaned_data["close_at"],
                title=form.cleaned_data["title"],
                actor=request.user,
            )
        except ValueError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, _("Attendance session window updated."))
            return redirect("attendance:attendance_session_detail", session.id)

    breadcrumb_items = _attendance_session_breadcrumbs(
        session,
        include_session_link=True,
    )
    breadcrumb_items.append({"label": _("Edit attendance session")})
    return render(request, "ui/pages/admin/attendance_window_form.html", {
        "form": form,
        "page_title": _("Edit attendance session"),
        "object_title": str(session),
        "notice": _("Changing a session window never deletes attendance records."),
        "submit_label": _("Save changes"),
        "back_url": reverse("attendance:attendance_session_detail", args=[session.id]),
        "breadcrumb_items": breadcrumb_items,
    })


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
    session = get_object_or_404(
        AttendanceSession.objects.select_related(
            "cohort", "course_version__course", "semester", "period",
            "created_by", "opened_by", "closed_by",
        ),
        id=session_id,
    )
    record_projection = _attendance_record_projection(session)

    ctx = {
        "session": session,
        **record_projection,
        "period": session.period,
        "is_admin": is_admin(request.user),
        "back_url": reverse(
            "attendance:attendance_period_detail", args=[session.period_id]
        ) if session.period_id else reverse("attendance:attendance_session_list"),
        "breadcrumb_items": _attendance_session_breadcrumbs(
            session,
            include_session_link=True,
        ) + [{"label": _("Attendance records")}],
    }
    return render(request, "ui/pages/admin/attendance_detail.html", ctx)


def _attendance_record_projection(session: AttendanceSession) -> dict:
    records = list(
        session.records.select_related(
            "student", "scanned_by", "session__period",
        ).order_by("scanned_at")
    )
    counted_record_count = 0
    for record in records:
        record.exclusion_reason = attendance_record_exclusion_reason(record)
        record.is_counted = record.exclusion_reason is None
        counted_record_count += int(record.is_counted)
    raw_record_count = len(records)
    return {
        "records": records,
        "raw_record_count": raw_record_count,
        "counted_record_count": counted_record_count,
        "excluded_record_count": raw_record_count - counted_record_count,
    }


# ---------------------------------------------------------------------------
# Secretary scan
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_ATTENDANCE_STAFF)
def attendance_scan(request):
    open_sessions = AttendanceSession.objects.filter(
        status=SESSION_STATUS_OPEN,
    ).filter(
        Q(course_version__course__attendance_enabled=True)
        | Q(cohort__diploma__attendance_enabled=True)
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

    enrollments = list(student_profile.diploma_enrollments.filter(
        status=ENROLLMENT_STATUS_ACTIVE,
        cohort__diploma__attendance_enabled=True,
    ).select_related("cohort__diploma"))
    visible_semesters_by_enrollment = get_visible_semesters_by_enrollment(enrollments)

    # Reuse the canonical batch attendance summaries (one set-based pass per
    # semester) and the same required-sessions capping the evaluation uses, so
    # every diploma card shows exactly the percentage the evaluation uses
    # (DECISION §13 — one attendance calculation everywhere).
    pairs_by_semester: dict[int, list[DiplomaEnrollment]] = {}
    semester_by_id: dict[int, Semester] = {}
    cohort_ids: set[int] = set()
    for enrollment in enrollments:
        visible_semesters = visible_semesters_by_enrollment.get(enrollment.id, [])
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

    course_enrollments = list(student_profile.course_enrollments.filter(
        status=ENROLLMENT_STATUS_ACTIVE,
        course_version__course__attendance_enabled=True,
    ).select_related("course_version__course"))
    if course_enrollments:
        course_version_ids = {ce.course_version_id for ce in course_enrollments}
        course_sessions = AttendanceSession.objects.filter(
            course_version_id__in=course_version_ids,
            period__isnull=True,
        ).filter(ended_or_closed)
        session_totals = {
            row["course_version_id"]: row["total"]
            for row in course_sessions.values("course_version_id").annotate(total=Count("id"))
        }
        counted_in_evaluation = counted_attendance_record_q() & Q(
            session__in=course_sessions,
        )
        record_counts = {
            row["course_enrollment_id"]: row
            for row in AttendanceRecord.objects.filter(
                course_enrollment_id__in=[ce.id for ce in course_enrollments],
                session__course_version_id=F("course_enrollment__course_version_id"),
                session__period__isnull=True,
            ).values("course_enrollment_id").annotate(
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
        for ce in course_enrollments:
            counts = record_counts.get(ce.id, {})
            summary = _build_attendance_summary(
                session_totals.get(ce.course_version_id, 0),
                counts.get("present_count", 0),
                counts.get("late_count", 0),
                counts.get("excused_count", 0),
                raw_record_count=counts.get("raw_record_count", 0),
            )
            if not summary["total_sessions"] and not summary["raw_record_count"]:
                continue
            summaries.append({
                "enrollment": ce,
                "course_title": str(ce.course_version.course.title),
                **summary,
            })

    attendance_history_qs = student_attendance_history_queryset(student_profile)
    attendance_page, attendance_pagination_qs = paginate(
        request,
        attendance_history_qs,
        per_page=25,
    )
    annotate_attendance_history_records(attendance_page.object_list)
    attendance_totals = attendance_history_qs.aggregate(
        total=Count("id"),
        countable=Count("id", filter=counted_attendance_record_q()),
    )
    raw_attendance_record_count = attendance_totals["total"] or 0
    countable_attendance_record_count = attendance_totals["countable"] or 0

    ctx = {
        "summaries": summaries,
        "attendance_records": attendance_page,
        "attendance_pagination_qs": attendance_pagination_qs,
        "attendance_record_total": raw_attendance_record_count,
        "attendance_record_countable": countable_attendance_record_count,
        "attendance_record_excluded": max(
            raw_attendance_record_count - countable_attendance_record_count,
            0,
        ),
        "breadcrumb_items": [{"label": _("My Attendance")}],
    }
    return render(request, "ui/pages/student/attendance.html", ctx)
