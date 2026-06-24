"""Views for authentication, dashboards, and profile."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from apps.academics.constants import SCOPE_DIPLOMA_COHORT
from apps.academics.models import (
    Cohort,
    Course,
    Diploma,
    DiplomaEnrollment,
    Lecture,
    ModeratorStudentAssignment,
    TeacherAssignment,
)
from apps.academics.services import get_student_course_versions
from apps.applications.constants import (
    APP_STATUS_PENDING,
    APP_STATUS_UNDER_REVIEW,
    APP_STATUS_ACTIVATED,
    APP_STATUS_APPROVED,
    APP_STATUS_REJECTED,
    PROGRAM_APP_STATUS_SUBMITTED,
    PROGRAM_APP_STATUS_UNDER_REVIEW,
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_REJECTED,
    PROGRAM_APP_STATUS_ACTIVATED,
    PROGRAM_APP_STATUS_INVITED,
)
from apps.applications.models import Application, ProgramApplication
from apps.assessments.constants import ATTEMPT_STATUS_SUBMITTED, ATTEMPT_STATUS_UNDER_REVIEW
from apps.assessments.models import AssessmentAttempt
from apps.audit.models import AuditEvent
from apps.core.htmx import is_htmx
from apps.core.permissions import ROLE_EXAM_STAFF, is_admin, role_required
from apps.core.views import safe_next_url
from apps.entrance_exams.constants import ATTEMPT_STATUS_COMPLETED
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

from .constants import (
    ALL_ROLE_GROUPS,
    GROUP_ADMIN,
    GROUP_MODERATOR,
    GROUP_STUDENT,
    GROUP_SUPER_ADMIN,
    GROUP_TEACHER,
    USER_STATUS_CHOICES,
)
from .forms import EmailAuthenticationForm, ProfileForm, UserEditForm
from .models import User


class EmailLoginView(LoginView):
    """Login view that authenticates by email."""

    authentication_form = EmailAuthenticationForm
    template_name = "accounts/login.html"
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
    return render(request, "accounts/profile.html", {"form": form})


@login_required
def dashboard_router(request: HttpRequest) -> HttpResponse:
    """Generic dashboard view that delegates to the role-specific view."""
    return redirect(_dashboard_url_name(request.user))


@login_required
def admin_dashboard(request: HttpRequest) -> HttpResponse:
    context = _admin_dashboard_data()
    context["user_role"] = request.user.primary_role or _("Super Admin")
    context["show_role_label"] = True
    return render(request, "accounts/dashboards/admin.html", context)


@login_required
def moderator_dashboard(request: HttpRequest) -> HttpResponse:
    context = _dashboard_context(request.user)
    now = timezone.now()
    context["pending_applications_count"] = Application.objects.filter(
        status__in=[APP_STATUS_PENDING, APP_STATUS_UNDER_REVIEW]
    ).count()
    context["cohorts_starting_soon"] = (
        Cohort.objects.filter(is_active=True, start_date__gte=now.date())
        .order_by("start_date")[:5]
    )
    context["user_count"] = User.objects.filter(is_active=True).count()
    return render(request, "accounts/dashboards/moderator.html", context)


@login_required
def teacher_dashboard(request: HttpRequest) -> HttpResponse:
    return render(request, "accounts/dashboards/teacher.html", _dashboard_context(request.user))


@login_required
def student_dashboard(request: HttpRequest) -> HttpResponse:
    return render(request, "accounts/dashboards/student.html", _dashboard_context(request.user))


# --- Helpers ----------------------------------------------------------------
def _dashboard_context(user: User) -> dict:
    ctx = {
        "user_role": user.primary_role or _("Super Admin"),
        "show_role_label": True,
    }
    if user.is_staff or user.is_superuser:
        ctx["diploma_count"] = Diploma.objects.filter(is_active=True).count()
    elif hasattr(user, "student_profile"):
        ctx["course_count"] = get_student_course_versions(user.student_profile).count()
    return ctx


def _admin_dashboard_data() -> dict:
    """Build the admin dashboard context with aggregate queries.

    Designed to run in a bounded number of queries — no per-row loops.
    """
    now = timezone.now()
    today = now.date()

    # Batch counts into a few aggregation queries instead of many .count() calls.

    # 1) Single query: Application statuses
    app_status_counts = dict(
        Application.objects.values_list("status")
        .annotate(cnt=Count("id"))
        .values_list("status", "cnt")
    )

    # 2) Single query: ProgramApplication statuses
    prog_app_status_counts = dict(
        ProgramApplication.objects.values_list("status")
        .annotate(cnt=Count("id"))
        .values_list("status", "cnt")
    )

    # 3) Single query: EntranceAttempt statuses
    entrance_status_counts = dict(
        EntranceAttempt.objects.values_list("status")
        .annotate(cnt=Count("id"))
        .values_list("status", "cnt")
    )

    # 4) Single query: PaymentRecord statuses
    payment_status_counts = dict(
        PaymentRecord.objects.values_list("status")
        .annotate(cnt=Count("id"))
        .values_list("status", "cnt")
    )

    # 5) Single query: PaymentUploadRow unmatched count
    unmatched_upload_count = PaymentUploadRow.objects.filter(
        status=ROW_STATUS_UNMATCHED
    ).count()

    # 6) Single query: pending uploads (limit 5)
    pending_uploads = list(
        PaymentUpload.objects.filter(
            status__in=[UPLOAD_STATUS_PENDING, UPLOAD_STATUS_PREVIEWED]
        ).order_by("-created_at")[:5]
    )

    # 7) Single query: AssessmentAttempt in submitted/under_review
    assessment_ungraded = AssessmentAttempt.objects.filter(
        status__in=[ATTEMPT_STATUS_SUBMITTED, ATTEMPT_STATUS_UNDER_REVIEW]
    ).count()

    # 8) Single query: Cohorts starting soon + counts + select_related
    cohorts_starting_soon = list(
        Cohort.objects.filter(is_active=True, start_date__gte=today)
        .select_related("diploma")
        .annotate(application_count=Count("applications"))
        .order_by("start_date")[:5]
    )

    # 9) Single query: active diplomas count
    active_diploma_count = Diploma.objects.filter(is_active=True).count()

    # 10) Single query: standalone courses + unpublished lectures + course counts
    from django.db.models import Q
    course_stats = Course.objects.aggregate(
        standalone_count=Count("id", filter=Q(is_active=True, is_standalone=True)),
    )
    unpublished_lecture_count = Lecture.objects.filter(is_published=False).count()

    # 11) Single query: recent audit
    recent_audit = list(
        AuditEvent.objects.select_related("actor")
        .order_by("-created_at")[:10]
    )

    # 12) Single query: recent applications
    recent_applications = list(
        Application.objects.select_related("student", "cohort")
        .order_by("-created_at")[:10]
    )

    # 13) Single query: recent payments
    recent_payments = list(
        PaymentRecord.objects.select_related("student")
        .order_by("-created_at")[:10]
    )

    # Combined grade count from entrance attempts (already fetched in #3)
    entrance_ungraded = entrance_status_counts.get("completed", 0)

    return {
        "pending_app_count": app_status_counts.get(APP_STATUS_PENDING, 0),
        "under_review_app_count": app_status_counts.get(APP_STATUS_UNDER_REVIEW, 0),
        "approved_app_count": app_status_counts.get(APP_STATUS_APPROVED, 0),
        "rejected_app_count": app_status_counts.get(APP_STATUS_REJECTED, 0),
        "activated_app_count": app_status_counts.get(APP_STATUS_ACTIVATED, 0),
        "prog_submitted_count": prog_app_status_counts.get(PROGRAM_APP_STATUS_SUBMITTED, 0),
        "prog_under_review_count": prog_app_status_counts.get(PROGRAM_APP_STATUS_UNDER_REVIEW, 0),
        "prog_invited_count": prog_app_status_counts.get(PROGRAM_APP_STATUS_INVITED, 0),
        "prog_accepted_count": prog_app_status_counts.get(PROGRAM_APP_STATUS_ACCEPTED, 0),
        "prog_rejected_count": prog_app_status_counts.get(PROGRAM_APP_STATUS_REJECTED, 0),
        "prog_activated_count": prog_app_status_counts.get(PROGRAM_APP_STATUS_ACTIVATED, 0),
        "entrance_assigned_count": entrance_status_counts.get("assigned", 0),
        "entrance_in_progress_count": entrance_status_counts.get("in_progress", 0),
        "entrance_completed_count": entrance_status_counts.get("completed", 0),
        "entrance_graded_count": entrance_status_counts.get("graded", 0),
        "paid_count": payment_status_counts.get(PAYMENT_STATUS_PAID, 0),
        "unpaid_count": payment_status_counts.get(PAYMENT_STATUS_UNPAID, 0),
        "unmatched_upload_count": unmatched_upload_count,
        "pending_uploads": pending_uploads,
        "active_diploma_count": active_diploma_count,
        "standalone_course_count": course_stats.get("standalone_count", 0),
        "unpublished_lecture_count": unpublished_lecture_count,
        "cohorts_starting_soon": cohorts_starting_soon,
        "assessment_ungraded": assessment_ungraded,
        "entrance_ungraded": entrance_ungraded,
        "recent_audit": recent_audit,
        "recent_applications": recent_applications,
        "recent_payments": recent_payments,
        "now": now,
    }


# ---------------------------------------------------------------------------
# Admin: User management
# ---------------------------------------------------------------------------

@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_user_list(request):
    current_user = request.user
    q = request.GET.get("q", "")
    role_filter = request.GET.get("role", "")
    status_filter = request.GET.get("status", "")
    cohort_filter = request.GET.get("cohort", "")
    diploma_filter = request.GET.get("diploma", "")
    course_filter = request.GET.get("course", "")
    payment_filter = request.GET.get("payment", "")

    # ponytail: scoped base queryset, upgrade to service if reused elsewhere
    if is_admin(current_user):
        users = User.objects.all()
    elif current_user.primary_role == GROUP_TEACHER:
        cohort_ids = TeacherAssignment.objects.filter(
            teacher=current_user, scope_type=SCOPE_DIPLOMA_COHORT, active=True
        ).values_list("cohort_id", flat=True)
        users = User.objects.filter(
            student_profile__diploma_enrollments__cohort_id__in=list(cohort_ids)
        )
    elif current_user.primary_role == GROUP_MODERATOR:
        student_user_ids = ModeratorStudentAssignment.objects.filter(
            moderator=current_user, active=True
        ).values_list("student__user_id", flat=True)
        users = User.objects.filter(id__in=list(student_user_ids))
    else:
        users = User.objects.none()

    users = users.prefetch_related("groups").distinct()

    # Filters (order matters: filter before search)
    if role_filter:
        users = users.filter(groups__name=role_filter)
    if status_filter:
        users = users.filter(status=status_filter)
    if cohort_filter:
        users = users.filter(student_profile__diploma_enrollments__cohort_id=cohort_filter)
    if diploma_filter:
        users = users.filter(student_profile__diploma_enrollments__cohort__diploma_id=diploma_filter)
    if course_filter:
        users = users.filter(student_profile__course_enrollments__course_version__course_id=course_filter)
    if payment_filter:
        if payment_filter == "paid":
            users = users.filter(student_profile__payment_records__status=PAYMENT_STATUS_PAID)
        elif payment_filter == "unpaid":
            users = users.filter(student_profile__payment_records__status=PAYMENT_STATUS_UNPAID)

    # Search
    if q:
        users = users.filter(
            Q(full_name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q) | Q(national_id__icontains=q)
        )

    # Pagination
    paginator = Paginator(users, 25)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    # Build filter options
    role_choices = [
        {"value": name, "label": _(name), "selected": role_filter == name}
        for name in (GROUP_SUPER_ADMIN, GROUP_ADMIN, GROUP_MODERATOR, GROUP_TEACHER, GROUP_STUDENT)
    ]
    status_choices = [
        {"value": code, "label": str(label), "selected": status_filter == code}
        for code, label in USER_STATUS_CHOICES
    ]

    if is_admin(current_user):
        cohorts = Cohort.objects.all()
        diplomas = Diploma.objects.all()
    elif current_user.primary_role == GROUP_TEACHER:
        cohorts = Cohort.objects.filter(
            teacher_assignments_new__teacher=current_user, teacher_assignments_new__active=True
        )
        diplomas = Diploma.objects.filter(cohorts__in=cohorts).distinct()
    elif current_user.primary_role == GROUP_MODERATOR:
        cids = ModeratorStudentAssignment.objects.filter(
            moderator=current_user, active=True
        ).values_list("cohort_id", flat=True)
        cohorts = Cohort.objects.filter(id__in=list(cids))
        diplomas = Diploma.objects.filter(cohorts__in=cohorts).distinct()
    else:
        cohorts = Cohort.objects.none()
        diplomas = Diploma.objects.none()

    cohort_choices = [
        {"value": str(c.id), "label": str(c), "selected": cohort_filter == str(c.id)}
        for c in cohorts
    ]
    diploma_choices = [
        {"value": str(d.id), "label": str(d), "selected": diploma_filter == str(d.id)}
        for d in diplomas
    ]

    standalone_courses = Course.objects.filter(is_standalone=True)
    course_choices = [
        {"value": str(c.id), "label": str(c), "selected": course_filter == str(c.id)}
        for c in standalone_courses
    ]

    payment_choices = [
        {"value": "unpaid", "label": _("Unpaid"), "selected": payment_filter == "unpaid"},
        {"value": "paid", "label": _("Paid"), "selected": payment_filter == "paid"},
    ]

    filters = [
        {"name": "role", "label": _("Role"), "value": role_filter, "options": role_choices},
        {"name": "status", "label": _("Status"), "value": status_filter, "options": status_choices},
        {"name": "cohort", "label": _("Cohort"), "value": cohort_filter, "options": cohort_choices},
        {"name": "diploma", "label": _("Diploma"), "value": diploma_filter, "options": diploma_choices},
        {"name": "course", "label": _("Course"), "value": course_filter, "options": course_choices},
        {"name": "payment", "label": _("Payment"), "value": payment_filter, "options": payment_choices},
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
    }

    if is_htmx(request):
        return render(request, "accounts/partials/_user_list_table.html", ctx)
    return render(request, "accounts/admin_user_list.html", ctx)


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_user_detail(request, user_id):
    user_obj = get_object_or_404(User.objects.prefetch_related("groups"), id=user_id)
    return render(request, "accounts/admin_user_detail.html", {"user_obj": user_obj})


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_user_edit(request, user_id):
    user_obj = get_object_or_404(User, id=user_id)
    next_url = safe_next_url(request)
    if request.method == "POST":
        form = UserEditForm(request.POST, instance=user_obj)
        if form.is_valid():
            form.save()
            messages.success(request, _("User updated."))
            return redirect(next_url) if next_url else redirect("accounts:admin_user_detail", user_id=user_obj.id)
    else:
        form = UserEditForm(instance=user_obj)
    return render(request, "accounts/admin_user_form.html", {"form": form, "user_obj": user_obj, "next": next_url})


def _dashboard_url_name(user: User) -> str:
    if user.is_superuser or user.primary_role in (GROUP_SUPER_ADMIN, GROUP_ADMIN):
        return "accounts:dashboard_admin"
    role = user.primary_role
    if role == GROUP_MODERATOR:
        return "accounts:dashboard_moderator"
    if role == GROUP_TEACHER:
        return "accounts:dashboard_teacher"
    if role == GROUP_STUDENT:
        return "accounts:dashboard_student"
    return "core:landing"
