"""Views for authentication, dashboards, and profile."""

from __future__ import annotations

import csv
import json
from urllib.parse import quote

from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.db.models import Count, Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from apps.academics.constants import SCOPE_COURSE_VERSION, SCOPE_DIPLOMA_COHORT
from apps.academics.models import (
    Cohort,
    Course,
    CourseVersion,
    Diploma,
    Lecture,
    ModeratorAssignment,
    StudentCluster,
    StudentClusterMembership,
    TeacherAssignment,
)
from apps.academics.services import get_student_course_versions
from apps.ai.models import GeminiReview
from apps.ai.prompts import DEFAULT_PROMPT_VERSION
from apps.applications.constants import (
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_ACTIVATED,
    PROGRAM_APP_STATUS_INVITED,
    PROGRAM_APP_STATUS_REJECTED,
    PROGRAM_APP_STATUS_SUBMITTED,
    PROGRAM_APP_STATUS_UNDER_REVIEW,
)
from apps.applications.models import ProgramApplication, ProgramApplicationDecision
from apps.applications.models import ProgramApplicationEmailLog
from apps.assessments.constants import ATTEMPT_STATUS_SUBMITTED, ATTEMPT_STATUS_UNDER_REVIEW
from apps.assessments.models import AssessmentAttempt
from apps.audit.constants import (
    ACTION_CLUSTER_CREATED,
    ACTION_CLUSTER_MEMBERSHIP_ADDED,
    ACTION_CLUSTER_MEMBERSHIP_REMOVED,
    ACTION_STAFF_ASSIGNMENT_CREATED,
    ACTION_STAFF_ASSIGNMENT_REVOKED,
    ACTION_USER_CREATED,
    ACTION_USER_DELETED,
    ACTION_USER_UPDATED,
)
from apps.audit.models import AuditEvent
from apps.audit.services import record_audit_event
from apps.communications.email_message import EmailMessage as CommEmailMessage
from apps.core.htmx import is_htmx
from apps.core.models import LandingAnnouncement
from apps.core.permissions import ROLE_ATTENDANCE_STAFF, ROLE_EXAM_STAFF, ROLE_SENIOR_STAFF, is_student, role_required
from apps.core.views import safe_next_url
from apps.entrance_exams.models import EntranceAttempt
from apps.payments.constants import (
    PAYMENT_STATUS_PAID,
    PAYMENT_STATUS_UNPAID,
    ROW_STATUS_UNMATCHED,
    UPLOAD_STATUS_PENDING,
    UPLOAD_STATUS_PREVIEWED,
)
from apps.payments.models import PaymentRecord, PaymentUpload, PaymentUploadRow
from apps.students.models import StudentProfile
from apps.attendance.models import AttendanceSession
from apps.attendance.models import AttendanceRecord
from apps.certificates.models import GeneratedCertificate
from apps.progression.models import StudentSemesterProgress
from apps.progression.constants import REJOIN_STATUS_PLANNED
from apps.progression.models import SemesterRejoinPlan

from .constants import (
    GROUP_ADMIN,
    GROUP_MODERATOR,
    GROUP_REVIEWER,
    GROUP_SECRETARY,
    GROUP_STUDENT,
    GROUP_SUPER_ADMIN,
    GROUP_TEACHER,
    STAFF_ROLE_GROUPS,
    USER_STATUS_CHOICES,
)
from .forms import EmailAuthenticationForm, ProfileForm, UserEditForm, UserUploadForm
from .models import User, UserUpload
from .services import confirm_upload, create_upload, preview_upload, send_activation_email
from .tokens import validate_activation_token


class EmailLoginView(LoginView):
    """Login view that authenticates by email."""

    authentication_form = EmailAuthenticationForm
    template_name = "ui/pages/public/login.html"
    redirect_authenticated_user = True
    next_page = reverse_lazy("accounts:post_login")


class EmailLogoutView(LogoutView):
    next_page = reverse_lazy("core:landing")


@login_required
def post_login(request: HttpRequest) -> HttpResponse:
    """Send the user to the correct dashboard for their role."""
    return redirect(_dashboard_url_name(request.user))


@login_required
@require_http_methods(["GET", "POST"])
def profile(request: HttpRequest) -> HttpResponse:
    """View and edit the current user's basic profile."""
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, _("Profile updated."))
            return redirect("accounts:profile")
    else:
        form = ProfileForm(instance=request.user)
    template = "ui/pages/student/account_profile.html" if is_student(request.user) and not request.user.is_staff else "ui/pages/admin/profile.html"
    return render(request, template, {"form": form})


@login_required
def dashboard_router(request: HttpRequest) -> HttpResponse:
    """Generic dashboard view that delegates to the role-specific view."""
    return redirect(_dashboard_url_name(request.user))


@login_required
@role_required(GROUP_SUPER_ADMIN, GROUP_ADMIN)
def admin_dashboard(request: HttpRequest) -> HttpResponse:
    context = _admin_dashboard_data()
    context["user_role"] = request.user.primary_role or _("Super Admin")
    context["show_role_label"] = True
    context["dashboard_kind"] = "admin"
    return render(request, "ui/pages/admin/dashboard.html", context)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_settings(request: HttpRequest) -> HttpResponse:
    context = _admin_settings_data()
    return render(request, "ui/pages/admin/settings.html", context)


@login_required
@role_required(GROUP_MODERATOR)
def moderator_dashboard(request: HttpRequest) -> HttpResponse:
    context = _dashboard_context(request.user)
    context["dashboard_kind"] = "moderator"
    return render(request, "ui/pages/admin/dashboard.html", context)


@login_required
@role_required(GROUP_TEACHER)
def teacher_dashboard(request: HttpRequest) -> HttpResponse:
    ctx = _dashboard_context(request.user)
    ctx["dashboard_kind"] = "teacher"
    return render(request, "ui/pages/admin/dashboard.html", ctx)


@login_required
@role_required(*ROLE_ATTENDANCE_STAFF)
def secretary_dashboard(request: HttpRequest) -> HttpResponse:
    now = timezone.now()
    today = now.date()
    sessions = AttendanceSession.objects.filter(
        date__gte=today,
    ).select_related("cohort", "course_version").order_by("date", "open_at")[:5]
    attendance_exception_count = AttendanceSession.objects.filter(
        status="scheduled", date__lt=today,
    ).count()
    return render(request, "ui/pages/admin/dashboard.html", {
        "dashboard_kind": "secretary",
        "upcoming_sessions": sessions,
        "session_count": AttendanceSession.objects.filter(date=today).count(),
        "attendance_exception_count": attendance_exception_count,
        "user_role": request.user.primary_role,
        "show_role_label": True,
    })


# --- Helpers ----------------------------------------------------------------
def _dashboard_context(user: User) -> dict:
    ctx = {
        "user_role": user.primary_role or _("Super Admin"),
        "show_role_label": True,
    }
    if user.primary_role == GROUP_MODERATOR:
        ctx["assignment_count"] = ModeratorAssignment.objects.filter(moderator=user, active=True).count()
    elif user.primary_role == GROUP_TEACHER:
        ctx["assignment_count"] = TeacherAssignment.objects.filter(teacher=user, active=True).count()
    elif user.is_staff or user.is_superuser:
        ctx["diploma_count"] = Diploma.objects.filter(is_active=True).count()
    elif hasattr(user, "student_profile"):
        ctx["course_count"] = get_student_course_versions(user.student_profile).count()
    return ctx


def _admin_settings_data() -> dict:
    ai_review_totals = GeminiReview.objects.aggregate(
        total=Count("id"),
        pending=Count("id", filter=Q(decision="pending")),
        accepted=Count("id", filter=Q(decision="accepted")),
        rejected=Count("id", filter=Q(decision="rejected")),
    )
    last_review = GeminiReview.objects.order_by("-created_at").values("created_at", "decision").first()
    email_health = CommEmailMessage.objects.aggregate(
        total=Count("id"),
        sent=Count("id", filter=Q(status="sent")),
        failed=Count("id", filter=Q(status="failed")),
    )
    last_email = CommEmailMessage.objects.order_by("-created_at").values("to_email", "created_at", "status").first()
    return {
        "ai_config": {
            "enabled": bool(django_settings.GEMINI_API_KEY),
            "provider": "Gemini", "model": django_settings.GEMINI_MODEL,
            "timeout": django_settings.GEMINI_TIMEOUT_SECONDS,
            "prompt_version": DEFAULT_PROMPT_VERSION,
            "review_count": ai_review_totals["total"], "review_pending": ai_review_totals["pending"],
            "review_accepted": ai_review_totals["accepted"], "review_rejected": ai_review_totals["rejected"],
            "last_review_at": last_review["created_at"] if last_review else None,
            "last_review_decision": last_review["decision"] if last_review else None,
        },
        "site_config": {
            "site_name": django_settings.SITE_NAME, "site_name_ar": django_settings.SITE_NAME_AR,
            "timezone": django_settings.TIME_ZONE, "language_code": "ar",
            "languages": dict(django_settings.LANGUAGES),
            "default_from_email": django_settings.DEFAULT_FROM_EMAIL,
            "email_backend": django_settings.EMAIL_BACKEND.split(".")[-1],
            "storage_backend": getattr(django_settings, "STORAGE_BACKEND", "local"),
            "email_total": email_health["total"], "email_sent": email_health["sent"],
            "email_failed": email_health["failed"],
            "last_email_to": last_email["to_email"] if last_email else None,
            "last_email_at": last_email["created_at"] if last_email else None,
            "last_email_status": last_email["status"] if last_email else None,
        },
        "active_announcement": LandingAnnouncement.current(),
    }


def _admin_dashboard_data() -> dict:
    """Build the admin dashboard context with aggregate queries.

    Designed to run in a bounded number of queries — no per-row loops.
    """
    today = timezone.now().date()

    # Batch counts into a few aggregation queries instead of many .count() calls.

    # 1) Single query: ProgramApplication statuses
    prog_app_status_counts = dict(
        ProgramApplication.objects.values_list("status")
        .annotate(cnt=Count("id"))
        .values_list("status", "cnt")
    )

    # 3) Single query: PaymentUploadRow unmatched count
    unmatched_upload_count = PaymentUpload.objects.filter(
        rows__status=ROW_STATUS_UNMATCHED
    ).distinct().count()
    attendance_exception_count = AttendanceSession.objects.filter(
        status="scheduled", date__lt=today,
    ).count()
    progression_exception_count = SemesterRejoinPlan.objects.filter(
        status=REJOIN_STATUS_PLANNED,
    ).count()
    failed_email_count = ProgramApplicationEmailLog.objects.filter(status="failed").count()

    # 4) Single query: AssessmentAttempt in submitted/under_review
    assessment_ungraded = AssessmentAttempt.objects.filter(
        status__in=[ATTEMPT_STATUS_SUBMITTED, ATTEMPT_STATUS_UNDER_REVIEW]
    ).count()

    # 5) Single query: unpublished lectures count
    unpublished_lecture_count = Lecture.objects.filter(is_published=False).count()

    # 6) Single query: recent program applications
    recent_applications = list(
        ProgramApplication.objects.select_related("student__user", "cohort__diploma", "course")
        .order_by("-created_at")[:10]
    )

    return {
        "prog_submitted_count": prog_app_status_counts.get(PROGRAM_APP_STATUS_SUBMITTED, 0),
        "prog_under_review_count": prog_app_status_counts.get(PROGRAM_APP_STATUS_UNDER_REVIEW, 0),
        "prog_accepted_count": prog_app_status_counts.get(PROGRAM_APP_STATUS_ACCEPTED, 0),
        "prog_activated_count": prog_app_status_counts.get(PROGRAM_APP_STATUS_ACTIVATED, 0),
        "unmatched_upload_count": unmatched_upload_count,
        "attendance_exception_count": attendance_exception_count,
        "progression_exception_count": progression_exception_count,
        "failed_email_count": failed_email_count,
        "unpublished_lecture_count": unpublished_lecture_count,
        "assessment_ungraded": assessment_ungraded,
        "recent_applications": recent_applications,
    }


# ---------------------------------------------------------------------------
# Admin: User management
# ---------------------------------------------------------------------------

@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_student_list(request):
    q = request.GET.get("q", "")
    status_filter = request.GET.get("status", "")

    users = User.objects.filter(
        Q(groups__name=GROUP_STUDENT) | Q(student_profile__isnull=False)
    )

    users = users.prefetch_related("groups").distinct()

    if status_filter:
        users = users.filter(status=status_filter)

    if q:
        users = users.filter(
            Q(full_name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q) | Q(national_id__icontains=q)
        )

    users = users.order_by("-date_joined", "full_name")

    paginator = Paginator(users, 15)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    status_choices = [
        {"value": code, "label": str(label), "selected": status_filter == code}
        for code, label in USER_STATUS_CHOICES
    ]

    filters = [
        {"name": "status", "label": _("Status"), "value": status_filter, "options": status_choices},
    ]

    pagination_qs = request.GET.copy()
    if "page" in pagination_qs:
        del pagination_qs["page"]
    pagination_qs = pagination_qs.urlencode()

    ctx = {
        "page_obj": page_obj,
        "search_value": q,
        "filters": filters,
        "pagination_qs": pagination_qs,
        "student_list": True,
    }

    if is_htmx(request):
        return render(request, "ui/fragments/admin/_student_table.html", ctx)
    return render(request, "ui/pages/admin/student_list.html", ctx)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_user_create(request, user_type):
    if user_type not in ("students", "staff"):
        raise Http404
    role = GROUP_STUDENT if user_type == "students" else GROUP_ADMIN
    form = UserEditForm(
        request.POST or None,
        instance=User(),
        initial={"role": role},
        staff_only=user_type == "staff",
    )
    if request.method == "POST" and form.is_valid():
        if (user_type == "students") != (form.cleaned_data.get("role") == GROUP_STUDENT):
            form.add_error("role", _("Select a role valid for this list."))
        else:
            user = form.save()
            record_audit_event(
                actor=request.user,
                action=ACTION_USER_CREATED,
                target_type="user",
                target_id=str(user.id),
                metadata={"role": user_type, "email": user.email},
            )
            messages.success(request, _("User created."))
            return redirect("accounts:admin_student_list" if user_type == "students" else "accounts:admin_staff_list")
    template = "ui/pages/admin/student_form.html" if user_type == "students" else "ui/pages/admin/staff_form.html"
    page_title = _("Create staff member") if user_type == "staff" else _("Create student")
    return render(request, template, {"form": form, "creating": True, "user_type": user_type, "title": page_title})


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_staff_list(request):
    q = request.GET.get("q", "")
    role_filter = request.GET.get("role", "")
    status_filter = request.GET.get("status", "")

    staff_role_names = list(STAFF_ROLE_GROUPS)
    users = User.objects.filter(groups__name__in=staff_role_names).prefetch_related("groups").distinct()

    if role_filter:
        users = users.filter(groups__name=role_filter)
    if status_filter:
        users = users.filter(status=status_filter)
    if q:
        users = users.filter(Q(full_name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q) | Q(national_id__icontains=q))

    users = users.order_by("full_name")

    paginator = Paginator(users, 15)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    role_choices = [
        {"value": name, "label": _(name), "selected": role_filter == name}
        for name in staff_role_names
    ]
    status_choices = [
        {"value": code, "label": str(label), "selected": status_filter == code}
        for code, label in USER_STATUS_CHOICES
    ]

    filters = [
        {"name": "role", "label": _("Role"), "value": role_filter, "options": role_choices},
        {"name": "status", "label": _("Status"), "value": status_filter, "options": status_choices},
    ]

    pagination_qs = request.GET.copy()
    if "page" in pagination_qs:
        del pagination_qs["page"]
    pagination_qs = pagination_qs.urlencode()

    ctx = {
        "page_obj": page_obj,
        "search_value": q,
        "filters": filters,
        "pagination_qs": pagination_qs,
        "staff_list": True,
    }

    if is_htmx(request):
        return render(request, "ui/fragments/admin/_staff_table.html", ctx)
    return render(request, "ui/pages/admin/staff_list.html", ctx)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_http_methods(["POST"])
def admin_user_delete(request, user_id):
    user_obj = get_object_or_404(User, id=user_id)
    is_student = user_obj.groups.filter(name=GROUP_STUDENT).exists()
    if user_obj == request.user:
        messages.error(request, _("You cannot delete your own account."))
    else:
        try:
            uid = user_obj.id
            email = user_obj.email
            user_obj.delete()
            record_audit_event(
                actor=request.user,
                action=ACTION_USER_DELETED,
                target_type="user",
                target_id=str(uid),
                metadata={"email": email},
            )
            messages.success(request, _("User deleted."))
        except ProtectedError:
            messages.error(request, _("This user cannot be deleted while referenced by existing records."))
    return redirect(reverse("accounts:admin_student_list" if is_student else "accounts:admin_staff_list"))


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_user_detail(request, user_id):
    user_obj = get_object_or_404(User.objects.select_related("student_profile").prefetch_related("groups"), id=user_id)
    back_url = safe_next_url(request)
    if not back_url:
        back_url = reverse("accounts:admin_student_list" if getattr(user_obj, "student_profile", None) else "accounts:admin_staff_list")
    profile = getattr(user_obj, "student_profile", None)
    context = {
        "user_obj": user_obj,
        "student_profile": profile,
        "back_url": back_url,
    }
    if profile:
        context.update({
            "applications": list(profile.program_applications.select_related("cohort__diploma", "course").order_by("-created_at")[:10]),
            "diploma_enrollments": list(profile.diploma_enrollments.select_related("cohort__diploma").order_by("-created_at")[:10]),
            "course_enrollments": list(profile.course_enrollments.select_related("course_version__course").order_by("-created_at")[:10]),
            "payments": list(profile.payment_records.order_by("-created_at")[:10]),
            "attendance_count": AttendanceRecord.objects.filter(student=profile).count(),
            "progression": list(StudentSemesterProgress.objects.filter(student=profile).select_related("semester", "cohort").order_by("-created_at")[:10]),
            "certificates": list(GeneratedCertificate.objects.filter(student=profile).select_related("diploma_enrollment__cohort__diploma").order_by("-created_at")[:10]),
            "assessment_attempts": list(AssessmentAttempt.objects.filter(student=user_obj).select_related("assessment").order_by("-created_at")[:10]),
            "entrance_attempts": list(EntranceAttempt.objects.filter(student=profile).select_related("exam").order_by("-created_at")[:10]),
            "staff_assignments": list(profile.moderator_assignments.select_related("moderator", "cohort").order_by("-created_at")[:10]),
            "decisions": list(ProgramApplicationDecision.objects.filter(program_application__student=profile).select_related("program_application", "actor").order_by("-created_at")[:10]),
            "application_list_url": f'{reverse("applications:program_list")}?student_id={profile.pk}',
            "progression_url": reverse("progression:admin_student_detail", args=[profile.pk]),
            "payment_list_url": f'{reverse("payments:record_list")}?student_q={profile.user.email}',
            "assessment_list_url": f'{reverse("assessments:admin_list")}?student_id={profile.user_id}',
            "entrance_list_url": f'{reverse("entrance_exams:admin_list")}?student_id={profile.pk}',
            "certificate_list_url": f'{reverse("certificates:admin_certificate_list")}?student_id={profile.pk}',
            "staff_assignment_url": reverse("progression:admin_student_detail", args=[profile.pk]),
            "enrollment_links": [
                *[
                    {"label": str(enrollment.cohort), "status": enrollment.get_status_display(), "url": f'{reverse("academics:admin_diploma_enrollment_list", args=[enrollment.cohort.diploma_id])}?q={quote(profile.full_name)}&cohort={enrollment.cohort_id}'}
                    for enrollment in profile.diploma_enrollments.select_related("cohort__diploma").order_by("-created_at")[:10]
                ],
                *[
                    {"label": str(enrollment.course_version.course), "status": enrollment.get_status_display(), "url": f'{reverse("academics:admin_course_enrollment_list", args=[enrollment.course_version.course_id])}?q={quote(profile.full_name)}'}
                    for enrollment in profile.course_enrollments.select_related("course_version__course").order_by("-created_at")[:10]
                ],
            ],
        })
    return render(
        request,
        "ui/pages/admin/student_detail.html",
        context,
    )


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_user_edit(request, user_id):
    user_obj = get_object_or_404(User, id=user_id)
    profile = getattr(user_obj, "student_profile", None)
    next_url = safe_next_url(request)
    if request.method == "POST":
        form = UserEditForm(request.POST, instance=user_obj, staff_only=profile is None)
        if form.is_valid():
            old_is_active = user_obj.is_active
            password_changed = bool(form.cleaned_data.get("new_password"))
            with transaction.atomic():
                form.save()
                if old_is_active != user_obj.is_active or password_changed:
                    record_audit_event(
                        actor=request.user,
                        action=ACTION_USER_UPDATED,
                        target_type="user",
                        target_id=str(user_obj.id),
                        metadata={
                            "is_active": user_obj.is_active,
                            "password_reset": password_changed,
                            "student_profile_id": profile.id if profile else None,
                        },
                    )
            messages.success(request, _("User updated."))
            return redirect(next_url) if next_url else redirect("accounts:admin_user_detail", user_id=user_obj.id)
    else:
        form = UserEditForm(instance=user_obj, staff_only=profile is None)
    template = "ui/pages/admin/student_form.html" if profile else "ui/pages/admin/staff_form.html"
    page_title = _("Edit student") if profile else _("Edit staff member")
    return render(request, template, {"form": form, "user_obj": user_obj, "next": next_url, "title": page_title})


@login_required
def admin_download_csv_template(request, kind):
    headers = {
        "students": ["email", "full_name", "phone", "national_id", "date_of_birth", "gender", "address", "education", "specialization", "employer", "current_job", "church", "priest_name", "priest_contact"],
        "payments": ["national_id", "amount", "payment_date", "external_reference", "email", "phone", "notes"],
    }.get(kind)
    if not headers:
        raise Http404
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{kind}_template.csv"'
    writer = csv.writer(response)
    writer.writerow(headers)
    if kind == "students":
        writer.writerow(["student@example.com", "John Doe", "+201234567890", "29001010100001", "2000-01-01", "male", "Cairo", "Bachelor", "CS", "Company", "Engineer", "St. Mary", "Fr. George", "+201111111111"])
    elif kind == "payments":
        writer.writerow(["29001010100001", "500.00", "2026-01-15", "INV-001", "student@example.com", "+201234567890", _("Tuition fee")])
    return response


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_download_student_csv_template(request):
    return admin_download_csv_template(request, "students")


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_student_import(request):
    """Upload CSV to bulk-import or bulk-update student users."""
    form = UserUploadForm()
    if request.method == "POST":
        form = UserUploadForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                content = request.FILES["csv_file"].read()
                upload = create_upload(
                    original_filename=request.FILES["csv_file"].name,
                    content=content,
                    uploaded_by=request.user,
                )
                upload = preview_upload(upload.id)
                if upload.status == UPLOAD_STATUS_PREVIEWED:
                    messages.success(request, _("File parsed. Review matches below."))
                else:
                    messages.warning(request, _("Some rows could not be matched. Review errors below."))
                return redirect("accounts:admin_student_import_detail", upload_id=upload.id)
            except ValueError as e:
                messages.error(request, str(e))
            except Exception as e:
                messages.error(request, _("Failed to process file: %(error)s") % {"error": str(e)})
        return render(request, "ui/pages/admin/import.html", {"form": form})


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_student_import_detail(request, upload_id):
    upload = get_object_or_404(UserUpload.objects.prefetch_related("rows__matched_user"), id=upload_id)
    return render(request, "ui/pages/admin/import.html", {"upload": upload})


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_http_methods(["POST"])
def admin_student_import_confirm(request, upload_id):
    upload = get_object_or_404(UserUpload, id=upload_id)
    if upload.status != UPLOAD_STATUS_PREVIEWED:
        messages.error(request, _("Upload is not in preview state."))
        return redirect("accounts:admin_student_import_detail", upload_id=upload.id)
    try:
        upload = confirm_upload(upload.id, confirmed_by=request.user)
        messages.success(
            request,
            _("Import completed: %(created)d created, %(updated)d updated.")
            % {"created": upload.created_rows, "updated": upload.updated_rows},
        )
    except ValueError as e:
        messages.error(request, str(e))
    return redirect("accounts:admin_student_import_detail", upload_id=upload.id)


# ---------------------------------------------------------------------------
# Admin: Staff assignments & student clusters
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_staff_assignments(request, user_id):
    """Manage teacher and moderator assignments for a staff member."""
    staff_user = get_object_or_404(User, id=user_id)
    staff_role = staff_user.primary_role
    back_url = safe_next_url(request) or reverse("accounts:admin_staff_list")

    teacher_assignments = TeacherAssignment.objects.filter(teacher=staff_user).select_related(
        "course", "course_version", "diploma", "semester", "cohort"
    )
    moderator_assignments = ModeratorAssignment.objects.filter(moderator=staff_user).select_related(
        "course_version", "cohort", "cluster"
    )

    cohorts = Cohort.objects.all()
    course_versions = CourseVersion.objects.select_related("course").all()
    clusters = StudentCluster.objects.select_related("cohort").all()

    ctx = {
        "staff_user": staff_user,
        "staff_role": staff_role,
        "back_url": back_url,
        "teacher_assignments": teacher_assignments,
        "moderator_assignments": moderator_assignments,
        "cohorts": cohorts,
        "course_versions": course_versions,
        "clusters": clusters,
        "SCOPE_COURSE_VERSION": SCOPE_COURSE_VERSION,
        "SCOPE_DIPLOMA_COHORT": SCOPE_DIPLOMA_COHORT,
        "GROUP_ADMIN": GROUP_ADMIN,
        "GROUP_SUPER_ADMIN": GROUP_SUPER_ADMIN,
        "GROUP_MODERATOR": GROUP_MODERATOR,
        "GROUP_TEACHER": GROUP_TEACHER,
    }
    return render(request, "ui/pages/admin/staff_assignments.html", ctx)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_http_methods(["POST"])
def admin_teacher_assignment_create(request, user_id):
    staff_user = get_object_or_404(User, id=user_id)
    if GROUP_TEACHER not in staff_user.groups.values_list("name", flat=True):
        messages.error(request, _("Target user is not a Teacher."))
        return redirect("accounts:admin_staff_assignments", user_id=staff_user.id)
    version_id = request.POST.get("course_version")
    if not version_id:
        messages.error(request, _("Course version is required."))
        return redirect("accounts:admin_staff_assignments", user_id=staff_user.id)
    version = get_object_or_404(CourseVersion, id=version_id)
    assignment = TeacherAssignment.objects.create(
        teacher=staff_user,
        scope_type=SCOPE_COURSE_VERSION,
        course_version=version,
        active=True,
        assigned_by=request.user,
    )
    record_audit_event(
        actor=request.user,
        action=ACTION_STAFF_ASSIGNMENT_CREATED,
        target_type="teacher_assignment",
        target_id=str(assignment.id),
        metadata={"teacher_id": str(staff_user.id), "course_version_id": str(version.id)},
    )
    messages.success(request, _("Teacher assignment created."))
    return redirect("accounts:admin_staff_assignments", user_id=staff_user.id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_http_methods(["POST"])
def admin_teacher_assignment_delete(request, user_id, assignment_id):
    assignment = get_object_or_404(TeacherAssignment, id=assignment_id, teacher_id=user_id)
    assignment.active = False
    assignment.revoked_by = request.user
    assignment.revoked_at = timezone.now()
    assignment.save()
    record_audit_event(
        actor=request.user,
        action=ACTION_STAFF_ASSIGNMENT_REVOKED,
        target_type="teacher_assignment",
        target_id=str(assignment.id),
        metadata={"teacher_id": str(user_id)},
    )
    messages.success(request, _("Teacher assignment revoked."))
    return redirect("accounts:admin_staff_assignments", user_id=user_id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_http_methods(["POST"])
def admin_moderator_assignment_create(request, user_id):
    staff_user = get_object_or_404(User, id=user_id)
    if GROUP_MODERATOR not in staff_user.groups.values_list("name", flat=True):
        messages.error(request, _("Target user is not a Moderator."))
        return redirect("accounts:admin_staff_assignments", user_id=staff_user.id)
    version_id = request.POST.get("course_version")
    scope = request.POST.get("scope", "cohort")
    cohort_id = request.POST.get("cohort")
    cluster_id = request.POST.get("cluster")
    if not version_id:
        messages.error(request, _("Course version is required."))
        return redirect("accounts:admin_staff_assignments", user_id=staff_user.id)
    if scope == "cohort" and not cohort_id:
        messages.error(request, _("Cohort is required for cohort-wide assignment."))
        return redirect("accounts:admin_staff_assignments", user_id=staff_user.id)
    if scope == "cluster" and not cluster_id:
        messages.error(request, _("Cluster is required for cluster-specific assignment."))
        return redirect("accounts:admin_staff_assignments", user_id=staff_user.id)
    version = get_object_or_404(CourseVersion, id=version_id)
    cluster = get_object_or_404(StudentCluster, id=cluster_id) if cluster_id else None
    cohort = cluster.cohort if cluster else get_object_or_404(Cohort, id=cohort_id)
    assignment = ModeratorAssignment.objects.create(
        moderator=staff_user,
        course_version=version,
        cohort=cohort,
        cluster=cluster,
        active=True,
        assigned_by=request.user,
    )
    record_audit_event(
        actor=request.user,
        action=ACTION_STAFF_ASSIGNMENT_CREATED,
        target_type="moderator_assignment",
        target_id=str(assignment.id),
        metadata={"moderator_id": str(staff_user.id), "course_version_id": str(version.id), "cohort_id": str(cohort.id)},
    )
    messages.success(request, _("Moderator assignment created."))
    return redirect("accounts:admin_staff_assignments", user_id=staff_user.id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_http_methods(["POST"])
def admin_moderator_assignment_delete(request, user_id, assignment_id):
    assignment = get_object_or_404(ModeratorAssignment, id=assignment_id, moderator_id=user_id)
    assignment.active = False
    assignment.revoked_by = request.user
    assignment.revoked_at = timezone.now()
    assignment.save(update_fields=["active", "revoked_by", "revoked_at", "updated_at"])
    record_audit_event(
        actor=request.user,
        action=ACTION_STAFF_ASSIGNMENT_REVOKED,
        target_type="moderator_assignment",
        target_id=str(assignment.id),
        metadata={"moderator_id": str(user_id)},
    )
    messages.success(request, _("Moderator assignment revoked."))
    return redirect("accounts:admin_staff_assignments", user_id=user_id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_http_methods(["POST"])
def admin_student_cluster_create(request, cohort_id):
    cohort = get_object_or_404(Cohort, id=cohort_id)
    name = request.POST.get("name")
    if not name:
        messages.error(request, _("Cluster name is required."))
        return redirect("academics:admin_diploma_detail", diploma_id=cohort.diploma_id)
    cluster = StudentCluster.objects.create(name=name, cohort=cohort, created_by=request.user)
    record_audit_event(
        actor=request.user,
        action=ACTION_CLUSTER_CREATED,
        target_type="student_cluster",
        target_id=str(cluster.id),
        metadata={"cohort_id": str(cohort.id), "name": name},
    )
    messages.success(request, _("Cluster created."))
    return redirect(reverse("academics:admin_diploma_detail", kwargs={"diploma_id": cohort.diploma_id}))


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_http_methods(["POST"])
def admin_student_cluster_membership_add(request, cluster_id):
    cluster = get_object_or_404(StudentCluster, id=cluster_id)
    student_ids = request.POST.getlist("students")
    if not student_ids:
        messages.error(request, _("Select at least one student."))
        return redirect(reverse("academics:admin_diploma_detail", kwargs={"diploma_id": cluster.cohort.diploma_id}))
    added = 0
    for sid in student_ids:
        profile = get_object_or_404(StudentProfile, id=sid)
        __, created = StudentClusterMembership.objects.get_or_create(
            cluster=cluster, student=profile, defaults={"assigned_by": request.user}
        )
        if created:
            added += 1
    record_audit_event(
        actor=request.user,
        action=ACTION_CLUSTER_MEMBERSHIP_ADDED,
        target_type="student_cluster_membership",
        target_id=str(cluster.id),
        metadata={"count": added, "cohort_id": str(cluster.cohort_id)},
    )
    msg = _("%(count)d student(s) added to cluster.") % {"count": added}
    messages.success(request, msg)
    if is_htmx(request):
        response = render(request, "ui/fragments/admin/_cluster_members.html", {"cluster": cluster})
        response['HX-Trigger'] = json.dumps({'show-toast': {'message': msg, 'type': 'success'}})
        return response
    return redirect("academics:admin_diploma_detail", diploma_id=cluster.cohort.diploma_id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_http_methods(["POST"])
def admin_student_cluster_membership_remove(request, cluster_id, membership_id):
    membership = get_object_or_404(StudentClusterMembership, id=membership_id, cluster_id=cluster_id)
    membership.delete()
    record_audit_event(
        actor=request.user,
        action=ACTION_CLUSTER_MEMBERSHIP_REMOVED,
        target_type="student_cluster_membership",
        target_id=str(membership.id),
        metadata={"cluster_id": str(cluster_id), "student_id": str(membership.student_id)},
    )
    msg = _("Student removed from cluster.")
    messages.success(request, msg)
    if is_htmx(request):
        response = render(request, "ui/fragments/admin/_cluster_members.html", {"cluster": membership.cluster})
        response['HX-Trigger'] = json.dumps({'show-toast': {'message': msg, 'type': 'success'}})
        return response
    return redirect("academics:admin_diploma_detail", diploma_id=membership.cluster.cohort.diploma_id)


def _dashboard_url_name(user: User) -> str:
    if user.is_superuser or user.primary_role in (GROUP_SUPER_ADMIN, GROUP_ADMIN):
        return "accounts:dashboard_admin"
    role = user.primary_role
    if role == GROUP_MODERATOR:
        return "accounts:dashboard_moderator"
    if role == GROUP_TEACHER:
        return "accounts:dashboard_teacher"
    if role == GROUP_REVIEWER:
        return "assessments:reviewer_dashboard"
    if role == GROUP_SECRETARY:
        return "accounts:dashboard_secretary"
    if role == GROUP_STUDENT:
        return "students:dashboard"
    return "core:landing"


@require_http_methods(["GET"])
def activate_account(request, uidb64, token):
    pk = validate_activation_token(uidb64, token)
    if pk is None:
        messages.error(request, _("Activation link is invalid or expired."))
        return redirect("core:landing")
    with transaction.atomic():
        user = User.objects.select_for_update().filter(pk=pk).first()
        if not user:
            messages.error(request, _("Activation link is invalid or expired."))
            return redirect("core:landing")
        if user.email_activated_at is not None:
            messages.info(request, _("Account is already activated. Please log in."))
            return redirect("accounts:login")
        user.is_active = True
        user.email_activated_at = timezone.now()
        user.save(update_fields=["is_active", "email_activated_at", "updated_at"])
    # handle post-activation application state machine
    from apps.applications.services import handle_user_account_activated
    handle_user_account_activated(user)
    auth_login(request, user)
    messages.success(request, _("Account activated successfully. Welcome!"))
    return redirect(_dashboard_url_name(user))


@require_http_methods(["GET", "POST"])
def resend_activation_email(request):
    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        user = User.objects.filter(email=email, is_active=False, email_activated_at__isnull=True).first()
        if user:
            send_activation_email(user)
        messages.success(request, _("If an inactive account exists, a new activation email was sent."))
        return redirect("accounts:login")
    return render(request, "ui/pages/public/resend_activation.html")
