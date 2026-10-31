"""Views for authentication, dashboards, and profile."""

from __future__ import annotations

import csv
from datetime import timedelta
from urllib.parse import quote

from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.db.models.deletion import ProtectedError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import (
    Cohort,
    Diploma,
    DiplomaEnrollment,
    Lecture,
    ModeratorAssignment,
    StudentCluster,
    StudentClusterMembership,
    TeacherAssignment,
)
from apps.academics.services import get_student_course_versions
from apps.applications.constants import (
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_ACTIVATED,
    PROGRAM_APP_STATUS_SUBMITTED,
    PROGRAM_APP_STATUS_UNDER_REVIEW,
)
from apps.applications.models import (
    ApplicationDocumentUpload,
    ProgramApplication,
    ProgramApplicationDecision,
    ProgramApplicationEmailLog,
)
from apps.applications.services import handle_user_account_activated
from apps.applications.views import review_queue_applications
from apps.assessments.constants import ATTEMPT_STATUS_SUBMITTED, ATTEMPT_STATUS_UNDER_REVIEW
from apps.assessments.models import AssessmentAttempt
from apps.attendance.constants import SESSION_STATUS_OPEN, SESSION_STATUS_SCHEDULED
from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.attendance.selectors import (
    annotate_attendance_history_records,
    effective_overdue_session_q,
    student_attendance_history_queryset,
)
from apps.attendance.services import counted_attendance_record_q
from apps.audit.constants import (
    ACTION_CLUSTER_CREATED,
    ACTION_CLUSTER_MEMBERSHIP_ADDED,
    ACTION_CLUSTER_MEMBERSHIP_REMOVED,
    ACTION_EMAIL_CONFIG_ACTIVATED,
    ACTION_EMAIL_CONFIG_CREATED,
    ACTION_EMAIL_CONFIG_DELETED,
    ACTION_EMAIL_CONFIG_TESTED,
    ACTION_EMAIL_CONFIG_UPDATED,
    ACTION_REDIS_KEY_CREATED,
    ACTION_REDIS_KEY_DELETED,
    ACTION_STAFF_ASSIGNMENT_CREATED,
    ACTION_STAFF_ASSIGNMENT_REVOKED,
    ACTION_USER_CREATED,
    ACTION_USER_UPDATED,
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    SECTION_ACCOUNTS,
    SECTION_SETTINGS,
)
from apps.audit.services import record_audit_event
from apps.certificates.models import GeneratedCertificate
from apps.communications.constants import (
    EMAIL_STATUS_FAILED,
    EMAIL_STATUS_PROCESSING,
    EMAIL_STATUS_QUEUED,
    EMAIL_STATUS_SENT,
    TEMPLATE_ACTIVATION,
)
from apps.communications.email_config import (
    activate_configuration,
    get_active_config,
    record_test_result,
    save_configuration,
    send_test_email,
    test_connection,
)
from apps.communications.email_message import EmailMessage as CommEmailMessage
from apps.communications.forms import EmailConfigurationForm
from apps.communications.models import EmailConfiguration, EmailTemplate
from apps.core import system_status
from apps.core.htmx import hx_toast, is_htmx
from apps.core.pagination import paginate
from apps.core.permissions import (
    ROLE_ATTENDANCE_STAFF,
    ROLE_EXAM_STAFF,
    ROLE_SENIOR_STAFF,
    infrastructure_required,
    is_infrastructure_manager,
    is_student,
    role_required,
    safe_int,
)
from apps.core.templatetags.ui_tags import status_icon, status_tone
from apps.core.views import safe_next_url
from apps.deployment.models import SystemJobRun
from apps.entrance_exams.models import EntranceAttempt
from apps.payments.constants import (
    ROW_STATUS_UNMATCHED,
    UPLOAD_STATUS_PREVIEWED,
)
from apps.payments.models import PaymentUpload
from apps.progression.constants import REJOIN_STATUS_PLANNED
from apps.progression.models import SemesterRejoinPlan, StudentSemesterProgress
from apps.students.models import StudentProfile

from .constants import (
    GROUP_ADMIN,
    GROUP_MODERATOR,
    GROUP_REVIEWER,
    GROUP_SECRETARY,
    GROUP_STUDENT,
    GROUP_SUPER_ADMIN,
    GROUP_TEACHER,
    STAFF_ROLE_GROUPS,
)
from .forms import ActivationConfirmForm, EmailAuthenticationForm, ProfileForm, UserEditForm, UserUploadForm
from .models import User, UserUpload
from .services import (
    confirm_upload,
    create_upload,
    delete_user_account,
    lock_identity_writes,
    preview_upload,
    send_activation_email,
)
from .tokens import validate_activation_token


def _user_edit_snapshot(user_obj, profile=None) -> dict[str, object]:
    """Return audit-safe field presence/value metadata without storing PII."""
    return {
        "email": user_obj.email,
        "full_name": user_obj.full_name,
        "full_name_ar": user_obj.full_name_ar,
        "is_active": user_obj.is_active,
        "role": user_obj.primary_role,
        "phone": profile.phone if profile else user_obj.phone,
        "country_code": profile.country_code if profile else user_obj.country_code,
        "national_id": profile.national_id if profile else user_obj.national_id,
        "date_of_birth": profile.date_of_birth if profile else None,
        "gender": profile.gender if profile else None,
        "address": profile.address if profile else None,
        "education": profile.education if profile else None,
        "specialization": profile.specialization if profile else None,
        "employer": profile.employer if profile else None,
        "current_job": profile.current_job if profile else None,
        "church": profile.church if profile else None,
        "priest_name": profile.priest_name if profile else None,
        "priest_contact": profile.priest_contact if profile else None,
    }


def _attach_form_validation_error(form, error: ValidationError) -> None:
    """Preserve field-specific errors raised by commit-time revalidation."""
    error_dict = getattr(error, "error_dict", None)
    if not error_dict:
        form.add_error(None, error)
        return
    for field, errors in error_dict.items():
        target_field = field if field in form.fields else None
        for field_error in errors:
            form.add_error(target_field, field_error)


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
    """View and edit the current user's basic profile.

    Phone is single-owner: for a student it lives on StudentProfile.phone only;
    for a staff user (no profile) it lives on User.phone. Email stays on User
    only.
    """
    profile = getattr(request.user, "student_profile", None)
    initial = {
        "full_name": profile.full_name if profile else request.user.full_name,
        "full_name_ar": request.user.full_name_ar
        or (profile.full_name if profile else request.user.full_name),
        "phone": profile.phone if profile else request.user.phone,
    }
    if request.method == "POST":
        form = ProfileForm(request.POST, user=request.user)
        if form.is_valid():
            full_name = form.cleaned_data["full_name"]
            phone = form.cleaned_data["phone"]
            try:
                with transaction.atomic():
                    lock_identity_writes()
                    phone = form.clean_phone()
                    request.user.full_name = full_name
                    request.user.full_name_ar = form.cleaned_data.get("full_name_ar", "")
                    if profile is not None:
                        profile.full_name = full_name
                        profile.phone = phone
                        profile.save(update_fields=["full_name", "phone", "updated_at"])
                        request.user.phone = ""
                        request.user.save(update_fields=["full_name", "full_name_ar", "phone", "updated_at"])
                    else:
                        request.user.phone = phone
                        request.user.save(update_fields=["full_name", "full_name_ar", "phone", "updated_at"])
            except ValidationError as exc:
                form.add_error("phone", exc)
            except IntegrityError:
                form.add_error("phone", _("This phone number is already in use."))
            else:
                messages.success(request, _("Profile updated."))
                return redirect("accounts:profile")
    else:
        form = ProfileForm(initial=initial, user=request.user)
    is_student_user = is_student(request.user)
    template = "ui/pages/student/account_profile.html" if is_student_user else "ui/pages/admin/profile.html"
    return render(
        request,
        template,
        {
            "form": form,
            # Email activation is a separate account concern, not an admissions gate.
            "needs_email_activation": is_student_user and request.user.email_activated_at is None,
        },
    )


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
@infrastructure_required
@require_http_methods(["GET", "POST"])
def admin_settings(request: HttpRequest) -> HttpResponse:
    tab = request.GET.get("tab", "email")
    if tab not in {"email", "site", "jobs", "redis"}:
        tab = "email"
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action.startswith("email_"):
            return _handle_email_settings_action(request)
        if action.startswith("redis_"):
            return _handle_redis_settings_action(request)
    return _render_settings(request, tab)


@login_required
@role_required(GROUP_MODERATOR)
def moderator_dashboard(request: HttpRequest) -> HttpResponse:
    context = _dashboard_context(request.user)
    context["dashboard_kind"] = "moderator"
    return render(request, "ui/pages/admin/dashboard.html", context)


@login_required
@role_required(*ROLE_ATTENDANCE_STAFF)
def secretary_dashboard(request: HttpRequest) -> HttpResponse:
    now = timezone.now()
    today = timezone.localdate(now)
    sessions = (
        AttendanceSession.objects.filter(
            date__gte=today,
            status__in=(SESSION_STATUS_SCHEDULED, SESSION_STATUS_OPEN),
        )
        .select_related("cohort", "course_version")
        .order_by("date", "open_at")[:5]
    )
    attendance_exception_count = AttendanceSession.objects.filter(
        effective_overdue_session_q(now),
    ).count()
    return render(
        request,
        "ui/pages/admin/dashboard.html",
        {
            "dashboard_kind": "secretary",
            "upcoming_sessions": sessions,
            "session_count": AttendanceSession.objects.filter(date=today).count(),
            "attendance_exception_count": attendance_exception_count,
            "user_role": request.user.primary_role,
            "show_role_label": True,
        },
    )


# --- Helpers ----------------------------------------------------------------
def _dashboard_context(user: User) -> dict:
    ctx = {
        "user_role": user.primary_role or _("Super Admin"),
        "show_role_label": True,
    }
    if user.primary_role == GROUP_MODERATOR:
        ctx["assignment_count"] = ModeratorAssignment.objects.filter(moderator=user, active=True).count()
    elif user.is_staff or user.is_superuser:
        ctx["diploma_count"] = Diploma.objects.filter(is_active=True).count()
    elif hasattr(user, "student_profile"):
        ctx["course_count"] = get_student_course_versions(user.student_profile).count()
    return ctx


def _throttle_allowed(key: str, seconds: int = 60) -> bool:
    """Best-effort cache throttle; a cache outage must not block activation."""
    try:
        return bool(cache.add(key, 1, timeout=seconds))
    except Exception:  # noqa: BLE001
        return True


def _email_health() -> dict:
    now = timezone.now()
    qs = CommEmailMessage.objects.all()

    def _window(hours: int) -> dict:
        scoped = qs.filter(created_at__gte=now - timedelta(hours=hours))
        return scoped.aggregate(
            total=Count("id"),
            sent=Count("id", filter=Q(status=EMAIL_STATUS_SENT)),
            failed=Count("id", filter=Q(status=EMAIL_STATUS_FAILED)),
            queued=Count("id", filter=Q(status=EMAIL_STATUS_QUEUED)),
            processing=Count("id", filter=Q(status=EMAIL_STATUS_PROCESSING)),
        )

    return {
        "windows": [
            {"label": _("Last 24 hours"), "stats": _window(24)},
            {"label": _("Last 7 days"), "stats": _window(24 * 7)},
            {"label": _("Last 30 days"), "stats": _window(24 * 30)},
        ],
        "last": qs.defer("body").order_by("-created_at").first(),
        "stuck": qs.filter(
            status__in=(EMAIL_STATUS_QUEUED, EMAIL_STATUS_PROCESSING),
            created_at__lt=now - timedelta(minutes=15),
        ).count(),
        "recent_failures": list(
            qs.filter(status=EMAIL_STATUS_FAILED).defer("body").order_by("-created_at")[:5]
        ),
    }


def _settings_site_config() -> dict:
    active = get_active_config()
    return {
        "site_name": django_settings.SITE_NAME,
        "site_name_ar": django_settings.SITE_NAME_AR,
        "timezone": django_settings.TIME_ZONE,
        "language_code": django_settings.LANGUAGE_CODE,
        "languages": dict(django_settings.LANGUAGES),
        "storage": _("R2") if django_settings.USE_R2_STORAGE else _("Local"),
        "email_source_label": _("Database") if active is not None else _("Environment"),
        "default_from_email": active.sender if active is not None else django_settings.DEFAULT_FROM_EMAIL,
        "email_backend": django_settings.EMAIL_BACKEND.rsplit(".", 1)[-1],
    }


def _settings_email_form(request: HttpRequest) -> EmailConfigurationForm:
    edit_id = safe_int(request.GET.get("edit"))
    if edit_id:
        config = get_object_or_404(EmailConfiguration, pk=edit_id)
        return EmailConfigurationForm(instance=config)
    return EmailConfigurationForm(
        instance=EmailConfiguration(
            host=django_settings.EMAIL_HOST,
            port=django_settings.EMAIL_PORT,
            username=django_settings.EMAIL_HOST_USER,
            use_tls=django_settings.EMAIL_USE_TLS,
            use_ssl=False,
            from_email=django_settings.DEFAULT_FROM_EMAIL,
            timeout=django_settings.EMAIL_TIMEOUT,
            is_active=False,
        )
    )


def _render_settings(
    request: HttpRequest,
    tab: str,
    *,
    email_form: EmailConfigurationForm | None = None,
) -> HttpResponse:
    context: dict[str, object] = {
        "tab": tab,
        "tabs": (
            ("email", _("Email")),
            ("site", _("Site")),
            ("jobs", _("Background jobs")),
            ("redis", _("Redis")),
        ),
        "email_log_url": reverse("communications:admin_email_log"),
    }
    if tab == "email":
        context["site_config"] = _settings_site_config()
        context["email_health"] = _email_health()
        context["email_configs"] = list(EmailConfiguration.objects.order_by("-is_active", "name"))
        context["active_email_config"] = get_active_config()
        context["email_form"] = email_form or _settings_email_form(request)
    if tab == "site":
        context["site_config"] = _settings_site_config()
        context["versions"] = system_status.get_versions()
    if tab == "jobs":
        celery_status = system_status.get_celery_status()
        context["celery_status"] = celery_status
        context["queue_depths"] = system_status.get_queue_depths(celery_status.get("queues", []))
        context["beat_schedule"] = system_status.get_beat_schedule()
        context["recent_jobs"] = list(SystemJobRun.objects.all()[:20])
    if tab == "redis":
        context["redis_overview"] = system_status.get_redis_overview()
        context["redis_instances"] = [
            context["redis_overview"]["cache"],
            context["redis_overview"]["broker"],
        ]
        pattern = request.GET.get("redis_q", "").strip()
        cursor = safe_int(request.GET.get("cursor"), 0) or 0
        context["redis_pattern"] = pattern
        context["redis_page"] = system_status.scan_cache_keys(pattern=pattern, cursor=cursor)
        redis_key = request.GET.get("redis_key", "").strip()
        if redis_key:
            context["redis_key_name"] = redis_key
            context["redis_key_detail"] = system_status.get_cache_key_value(redis_key)
    return render(request, "ui/pages/admin/settings.html", context)


def _settings_redirect(tab: str) -> HttpResponse:
    return redirect(f"{reverse('accounts:admin_settings')}?tab={tab}")


def _handle_email_settings_action(request: HttpRequest) -> HttpResponse:
    action = request.POST.get("action", "")
    if action == "email_save":
        config_id = safe_int(request.POST.get("config_id"))
        instance = get_object_or_404(EmailConfiguration, pk=config_id) if config_id else EmailConfiguration()
        form = EmailConfigurationForm(request.POST, instance=instance)
        if form.is_valid():
            saved = form.save(commit=False)
            save_configuration(saved)
            record_audit_event(
                request.user,
                ACTION_EMAIL_CONFIG_UPDATED if config_id else ACTION_EMAIL_CONFIG_CREATED,
                target_type="email_config",
                target_id=saved.pk,
                section=SECTION_SETTINGS,
                target_label=saved.name,
                metadata={
                    "changed_fields": list(form.changed_data),
                    "host": saved.host,
                    "port": saved.port,
                    "username_changed": "username" in form.changed_data,
                    "use_tls": saved.use_tls,
                    "use_ssl": saved.use_ssl,
                    "from_email": saved.from_email,
                    "from_name": saved.from_name,
                    "is_active": saved.is_active,
                    "password_changed": bool(form.cleaned_data.get("password")),
                },
            )
            messages.success(request, _("Email configuration saved."))
            return _settings_redirect("email")
        return _render_settings(request, "email", email_form=form)

    if action == "email_activate":
        config = get_object_or_404(EmailConfiguration, pk=safe_int(request.POST.get("config_id")))
        activate_configuration(config)
        record_audit_event(
            request.user,
            ACTION_EMAIL_CONFIG_ACTIVATED,
            target_type="email_config",
            target_id=config.pk,
            section=SECTION_SETTINGS,
            target_label=config.name,
            metadata={"host": config.host, "from_email": config.from_email},
        )
        messages.success(request, _("Email configuration activated."))
        return _settings_redirect("email")

    if action == "email_delete":
        config = get_object_or_404(EmailConfiguration, pk=safe_int(request.POST.get("config_id")))
        record_audit_event(
            request.user,
            ACTION_EMAIL_CONFIG_DELETED,
            target_type="email_config",
            target_id=config.pk,
            section=SECTION_SETTINGS,
            target_label=config.name,
            metadata={"host": config.host, "is_active": config.is_active},
        )
        config.delete()
        messages.success(request, _("Email configuration deleted."))
        return _settings_redirect("email")

    if action == "email_test":
        config_id = safe_int(request.POST.get("config_id"))
        config = get_object_or_404(EmailConfiguration, pk=config_id) if config_id else get_active_config()
        if config is None:
            messages.error(request, _("There is no active email configuration to test."))
            return _settings_redirect("email")
        test_email = (request.POST.get("test_email") or "").strip()
        ok, error = test_connection(config)
        if ok and test_email:
            try:
                validate_email(test_email)
            except ValidationError:
                messages.error(request, _("Enter a valid test recipient address."))
                return _settings_redirect("email")
            ok, error = send_test_email(config=config, to_email=test_email)
        record_test_result(config, ok=ok, error=error)

        record_audit_event(
            request.user,
            ACTION_EMAIL_CONFIG_TESTED,
            target_type="email_config",
            target_id=config.pk,
            section=SECTION_SETTINGS,
            target_label=config.name,
            metadata={"ok": ok, "error": error, "recipient": test_email},
            outcome=OUTCOME_SUCCESS if ok else OUTCOME_FAILURE,
        )
        if ok:
            messages.success(request, _("Email configuration test succeeded."))
        else:
            messages.error(request, _("Email configuration test failed: %(error)s") % {"error": error})
        return _settings_redirect("email")

    return _settings_redirect("email")


def _handle_redis_settings_action(request: HttpRequest) -> HttpResponse:
    action = request.POST.get("action", "")
    key = (request.POST.get("redis_key") or "").strip()
    reason = (request.POST.get("reason") or "").strip()
    if action == "redis_add":
        if system_status.is_protected_redis_key(key):
            messages.error(request, _("That key name is reserved for protected exam state."))
            return _settings_redirect("redis")
        value = request.POST.get("redis_value") or ""
        ttl = safe_int(request.POST.get("redis_ttl"), 0) or 0
        ok, error = system_status.set_cache_key(key, value, ttl)

        record_audit_event(
            request.user,
            ACTION_REDIS_KEY_CREATED,
            target_type="redis_key",
            target_id=key,
            section=SECTION_SETTINGS,
            target_label=key,
            metadata={"key": key, "ttl": ttl, "error": error},
            outcome=OUTCOME_SUCCESS if ok else OUTCOME_FAILURE,
        )
        if ok:
            messages.success(request, _("Redis key created."))
        else:
            messages.error(request, _("Could not create the Redis key: %(error)s") % {"error": error})
        return _settings_redirect("redis")

    if action == "redis_delete":
        protected = system_status.is_protected_redis_key(key)
        confirm_key = (request.POST.get("confirm_key") or "").strip()
        if protected and (not reason or confirm_key != key):
            messages.error(
                request,
                _("This key is protected. Type the exact key name and a reason before deleting it."),
            )
            return _settings_redirect("redis")
        ok, error = system_status.delete_cache_key(key)

        record_audit_event(
            request.user,
            ACTION_REDIS_KEY_DELETED,
            target_type="redis_key",
            target_id=key,
            section=SECTION_SETTINGS,
            target_label=key,
            metadata={"key": key, "reason": reason, "protected": protected, "error": error},
            outcome=OUTCOME_SUCCESS if ok else OUTCOME_FAILURE,
        )
        if ok:
            messages.success(request, _("Redis key deleted."))
        else:
            messages.error(request, _("Could not delete the Redis key: %(error)s") % {"error": error})
        return _settings_redirect("redis")

    return _settings_redirect("redis")


def _admin_dashboard_data() -> dict:
    """Build the admin dashboard context with aggregate queries.

    Designed to run in a bounded number of queries — no per-row loops.
    """
    now = timezone.now()

    # Batch counts into a few aggregation queries instead of many .count() calls.

    # 1) Single query: ProgramApplication statuses
    prog_app_status_counts = dict(
        ProgramApplication.objects.values_list("status")
        .annotate(cnt=Count("id"))
        .values_list("status", "cnt")
    )

    # 3) Single query: PaymentUploadRow unmatched count
    unmatched_upload_count = (
        PaymentUpload.objects.filter(rows__status=ROW_STATUS_UNMATCHED).distinct().count()
    )
    attendance_exception_count = AttendanceSession.objects.filter(
        effective_overdue_session_q(now),
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
        ProgramApplication.objects.select_related("student__user", "cohort__diploma", "course").order_by(
            "-created_at"
        )[:10]
    )

    return {
        "prog_submitted_count": prog_app_status_counts.get(PROGRAM_APP_STATUS_SUBMITTED, 0),
        "prog_under_review_count": prog_app_status_counts.get(PROGRAM_APP_STATUS_UNDER_REVIEW, 0),
        "application_review_count": review_queue_applications().count(),
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

    users = User.objects.filter(Q(groups__name=GROUP_STUDENT) | Q(student_profile__isnull=False))

    users = users.select_related("student_profile").prefetch_related("groups").distinct()

    if status_filter in ("active", "inactive"):
        users = users.filter(is_active=status_filter == "active")

    if q:
        users = users.filter(
            Q(full_name__icontains=q)
            | Q(email__icontains=q)
            | Q(student_profile__phone__icontains=q)
            | Q(national_id__icontains=q)
        )

    users = users.order_by("-date_joined", "full_name")

    page_obj, pagination_qs = paginate(request, users, per_page=15)

    status_choices = [
        {"value": value, "label": _(label), "selected": status_filter == value}
        for value, label in (("active", "Active"), ("inactive", "Inactive"))
    ]

    filters = [
        {"name": "status", "label": _("Status"), "value": status_filter, "options": status_choices},
    ]

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
@require_http_methods(["GET", "POST"])
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
            try:
                with transaction.atomic():
                    user = form.save()
            except ValidationError as exc:
                _attach_form_validation_error(form, exc)
            except IntegrityError:
                form.add_error(
                    None, _("The account could not be saved because an identity value is already in use.")
                )
            else:
                record_audit_event(
                    actor=request.user,
                    action=ACTION_USER_CREATED,
                    target_type="user",
                    target_id=str(user.id),
                    section=SECTION_ACCOUNTS,
                    target_label=str(user),
                    subject_type="user",
                    subject_id=str(user.id),
                    subject_label=str(user),
                    metadata={"role": user.primary_role, "active": user.is_active},
                )
                messages.success(request, _("User created."))
                return redirect(
                    "accounts:admin_student_list" if user_type == "students" else "accounts:admin_staff_list"
                )
    template = (
        "ui/pages/admin/student_form.html" if user_type == "students" else "ui/pages/admin/staff_form.html"
    )
    page_title = _("Create staff member") if user_type == "staff" else _("Create student")
    return render(
        request, template, {"form": form, "creating": True, "user_type": user_type, "title": page_title}
    )


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
    if status_filter in ("active", "inactive"):
        users = users.filter(is_active=status_filter == "active")
    if q:
        users = users.filter(
            Q(full_name__icontains=q)
            | Q(email__icontains=q)
            | Q(phone__icontains=q)
            | Q(national_id__icontains=q)
        )

    users = users.order_by("full_name")

    page_obj, pagination_qs = paginate(request, users, per_page=15)

    role_choices = [
        {"value": name, "label": _(name), "selected": role_filter == name} for name in staff_role_names
    ]
    status_choices = [
        {"value": value, "label": _(label), "selected": status_filter == value}
        for value, label in (("active", "Active"), ("inactive", "Inactive"))
    ]

    filters = [
        {"name": "role", "label": _("Role"), "value": role_filter, "options": role_choices},
        {"name": "status", "label": _("Status"), "value": status_filter, "options": status_choices},
    ]

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
    is_student = (
        user_obj.groups.filter(name=GROUP_STUDENT).exists()
        or StudentProfile.objects.filter(user=user_obj).exists()
    )
    if user_obj == request.user:
        messages.error(request, _("You cannot delete your own account."))
    elif is_student:
        try:
            delete_user_account(user_obj, request.user, require_student=True)
            messages.success(request, _("Student account deleted."))
        except (ProtectedError, ValueError) as exc:
            messages.error(request, str(exc))
    else:
        try:
            delete_user_account(user_obj, request.user)
            messages.success(request, _("User deleted."))
        except (ProtectedError, ValueError) as exc:
            messages.error(request, str(exc))
    return redirect(reverse("accounts:admin_student_list" if is_student else "accounts:admin_staff_list"))


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_http_methods(["POST"])
def admin_user_resend_activation(request, user_id):
    user_obj = get_object_or_404(User, id=user_id)
    is_student = (
        user_obj.groups.filter(name=GROUP_STUDENT).exists()
        or StudentProfile.objects.filter(user=user_obj).exists()
    )
    target_url = reverse("accounts:admin_student_list" if is_student else "accounts:admin_staff_list")
    if user_obj == request.user:
        messages.error(request, _("You cannot resend activation for your own account."))
    elif user_obj.email_activated_at is not None or user_obj.is_active:
        messages.error(request, _("This account is already active."))
    else:
        try:
            resent_count = CommEmailMessage.objects.filter(
                to_email=user_obj.email,
                template_slug=TEMPLATE_ACTIVATION,
            ).count()
            record = send_activation_email(
                user_obj,
                sent_by=request.user,
                idempotency_key=f"activation:resend:{user_obj.pk}:{resent_count}",
            )
            record_audit_event(
                actor=request.user,
                action=ACTION_USER_UPDATED,
                target_type="user",
                target_id=str(user_obj.id),
                section=SECTION_ACCOUNTS,
                target_label=str(user_obj),
                subject_type="user",
                subject_id=str(user_obj.id),
                subject_label=str(user_obj),
                metadata={"activation_email_resent": record is not None},
            )
            # When the template is missing/disabled the queued row is None; the
            # email-notice middleware reports it, so do not claim success here.
            if record is not None:
                messages.success(request, _("Activation email queued."))
        except Exception:  # noqa: BLE001
            messages.error(request, _("The activation email could not be queued. Please try again later."))
    return redirect(target_url)


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_user_detail(request, user_id):
    user_obj = get_object_or_404(
        User.objects.select_related("student_profile").prefetch_related("groups"), id=user_id
    )
    back_url = safe_next_url(request)
    if not back_url:
        back_url = reverse(
            "accounts:admin_student_list"
            if getattr(user_obj, "student_profile", None)
            else "accounts:admin_staff_list"
        )
    profile = getattr(user_obj, "student_profile", None)
    context = {
        "user_obj": user_obj,
        "student_profile": profile,
        "display_phone": profile.phone if profile else user_obj.phone,
        "back_url": back_url,
        "can_delete_student": bool(profile) and user_obj != request.user,
        "can_resend_activation": user_obj != request.user and user_obj.email_activated_at is None,
    }
    if profile:
        attendance_totals = AttendanceRecord.objects.filter(student=profile).aggregate(
            total=Count("id"),
            countable=Count("id", filter=counted_attendance_record_q()),
        )
        attendance_record_total = attendance_totals["total"] or 0
        attendance_record_countable = attendance_totals["countable"] or 0
        context.update(
            {
                "applications": list(
                    profile.program_applications.select_related("cohort__diploma", "course").order_by(
                        "-created_at"
                    )[:10]
                ),
                "diploma_enrollments": list(
                    profile.diploma_enrollments.select_related("cohort__diploma").order_by("-created_at")[:10]
                ),
                "course_enrollments": list(
                    profile.course_enrollments.select_related("course_version__course").order_by(
                        "-created_at"
                    )[:10]
                ),
                "payments": list(profile.payment_records.order_by("-created_at")[:10]),
                "attendance_summary": {
                    "total": attendance_record_total,
                    "countable": attendance_record_countable,
                    "excluded": max(attendance_record_total - attendance_record_countable, 0),
                },
                "attendance_records": annotate_attendance_history_records(
                    list(student_attendance_history_queryset(profile)[:10]),
                ),
                "progression": list(
                    StudentSemesterProgress.objects.filter(student=profile)
                    .select_related("semester", "cohort")
                    .order_by("-created_at")[:10]
                ),
                "certificates": list(
                    GeneratedCertificate.objects.filter(student=profile)
                    .select_related(
                        "diploma_enrollment__cohort__diploma",
                        "course_enrollment__course_version__course",
                    )
                    .order_by("-created_at")[:10]
                ),
                "assessment_attempts": list(
                    AssessmentAttempt.objects.filter(student=user_obj)
                    .select_related("assessment")
                    .order_by("-created_at")[:10]
                ),
                "entrance_attempts": list(
                    EntranceAttempt.objects.filter(student=profile)
                    .select_related("exam")
                    .order_by("-created_at")[:10]
                ),
                "staff_assignments": list(
                    profile.moderator_assignments.select_related("moderator", "cohort").order_by(
                        "-created_at"
                    )[:10]
                ),
                "decisions": list(
                    ProgramApplicationDecision.objects.filter(program_application__student=profile)
                    .select_related("program_application", "actor")
                    .order_by("-created_at")[:10]
                ),
                "document_uploads": list(
                    ApplicationDocumentUpload.objects.filter(
                        request__program_application__student=profile,
                    )
                    .select_related(
                        "request__program_application__cohort__diploma",
                        "request__program_application__course",
                    )
                    .order_by("-submitted_at")[:20]
                ),
                "application_list_url": f"{reverse('applications:program_list')}?student_id={profile.pk}",
                "progression_url": reverse("progression:admin_student_detail", args=[profile.pk]),
                "payment_list_url": f"{reverse('payments:record_list')}?student_q={profile.user.email}",
                "assessment_list_url": f"{reverse('assessments:admin_list')}?student_id={profile.user_id}",
                "entrance_list_url": f"{reverse('entrance_exams:admin_list')}?student_id={profile.pk}",
                "certificate_list_url": f"{reverse('certificates:admin_certificate_list')}?student_id={profile.pk}",
                "staff_assignment_url": reverse("progression:admin_student_detail", args=[profile.pk]),
                "enrollment_links": [
                    *[
                        {
                            "label": str(enrollment.cohort),
                            "status": enrollment.get_status_display(),
                            "url": f"{reverse('academics:admin_diploma_enrollment_list', args=[enrollment.cohort.diploma_id])}?q={quote(profile.full_name)}&cohort={enrollment.cohort_id}",
                        }
                        for enrollment in profile.diploma_enrollments.select_related(
                            "cohort__diploma"
                        ).order_by("-created_at")[:10]
                    ],
                    *[
                        {
                            "label": str(enrollment.course_version.course),
                            "status": enrollment.get_status_display(),
                            "url": f"{reverse('academics:admin_course_enrollment_list', args=[enrollment.course_version.course_id])}?q={quote(profile.full_name)}",
                        }
                        for enrollment in profile.course_enrollments.select_related(
                            "course_version__course"
                        ).order_by("-created_at")[:10]
                    ],
                ],
            }
        )
    return render(
        request,
        "ui/pages/admin/student_detail.html",
        context,
    )


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_user_edit(request, user_id):
    next_url = safe_next_url(request)
    if request.method == "POST":
        with transaction.atomic():
            lock_identity_writes()
            user_obj = get_object_or_404(
                User.objects.select_for_update().prefetch_related("groups"),
                id=user_id,
            )
            profile = getattr(user_obj, "student_profile", None)
            is_student_user = profile is not None or user_obj.groups.filter(name=GROUP_STUDENT).exists()
            form = UserEditForm(
                request.POST,
                instance=user_obj,
                staff_only=not is_student_user,
            )
            if form.is_valid():
                password_changed = bool(form.cleaned_data.get("new_password"))
                before = _user_edit_snapshot(user_obj, profile)
                try:
                    with transaction.atomic():
                        form.save()
                except ValidationError as exc:
                    _attach_form_validation_error(form, exc)
                except IntegrityError:
                    form.add_error(
                        None,
                        _("The account could not be saved because an identity value is already in use."),
                    )
                else:
                    after_profile = getattr(user_obj, "student_profile", None)
                    after = _user_edit_snapshot(user_obj, after_profile)
                    changed_fields = [
                        field for field, old_value in before.items() if old_value != after.get(field)
                    ]
                    if password_changed:
                        changed_fields.append("password")
                    if before.get("is_active") != after.get("is_active"):
                        changed_fields.append("activation")
                    if changed_fields:
                        record_audit_event(
                            actor=request.user,
                            action=ACTION_USER_UPDATED,
                            target_type="user",
                            target_id=str(user_obj.id),
                            section=SECTION_ACCOUNTS,
                            target_label=str(user_obj),
                            subject_type="user",
                            subject_id=str(user_obj.id),
                            subject_label=str(user_obj),
                            metadata={
                                "changed_fields": sorted(set(changed_fields)),
                                "password_reset": password_changed,
                                "student_profile_id": after_profile.id if after_profile else None,
                                "role_before": before.get("role"),
                                "role_after": after.get("role"),
                                "active_before": before.get("is_active"),
                                "active_after": after.get("is_active"),
                            },
                        )
                    messages.success(request, _("User updated."))
                    return (
                        redirect(next_url)
                        if next_url
                        else redirect("accounts:admin_user_detail", user_id=user_obj.id)
                    )
    else:
        user_obj = get_object_or_404(
            User.objects.select_related("student_profile").prefetch_related("groups"),
            id=user_id,
        )
        profile = getattr(user_obj, "student_profile", None)
        is_student_user = profile is not None or user_obj.groups.filter(name=GROUP_STUDENT).exists()
        form = UserEditForm(
            instance=user_obj,
            staff_only=not is_student_user,
        )
    template = "ui/pages/admin/student_form.html" if is_student_user else "ui/pages/admin/staff_form.html"
    page_title = _("Edit student") if is_student_user else _("Edit staff member")
    return render(
        request, template, {"form": form, "user_obj": user_obj, "next": next_url, "title": page_title}
    )


@login_required
def admin_download_csv_template(request, kind):
    headers = {
        "students": [
            "email",
            "full_name",
            "phone",
            "national_id",
            "date_of_birth",
            "gender",
            "address",
            "education",
            "specialization",
            "employer",
            "current_job",
            "church",
            "priest_name",
            "priest_contact",
        ],
        "payments": [
            "national_id",
            "amount",
            "payment_date",
            "external_reference",
            "email",
            "phone",
            "notes",
        ],
    }.get(kind)
    if not headers:
        raise Http404
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{kind}_template.csv"'
    writer = csv.writer(response)
    writer.writerow(headers)
    if kind == "students":
        writer.writerow(
            [
                "student@example.com",
                "John Doe",
                "+201234567890",
                "29001010100001",
                "2000-01-01",
                "male",
                "Cairo",
                "Bachelor",
                "CS",
                "Company",
                "Engineer",
                "St. Mary",
                "Fr. George",
                "+201111111111",
            ]
        )
    elif kind == "payments":
        writer.writerow(
            [
                "29001010100001",
                "500.00",
                "2026-01-15",
                "INV-001",
                "student@example.com",
                "+201234567890",
                _("Tuition fee"),
            ]
        )
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
            except Exception:  # noqa: BLE001
                messages.error(request, _("Failed to process file. Please review the file and try again."))
        return render(request, "ui/pages/admin/import.html", {"form": form})
    return render(request, "ui/pages/admin/import.html", {"form": form})


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_student_import_detail(request, upload_id):
    upload = get_object_or_404(UserUpload, id=upload_id)
    preview_rows = [
        {
            "row": row.row_number,
            "email": row.normalized_email,
            "status": row.status,
            "status_label": row.get_status_display(),
            "tone": status_tone(row.status),
            "icon": status_icon(row.status),
            "error": row.error_message,
        }
        for row in upload.rows.order_by("row_number")
    ]
    return render(
        request,
        "ui/pages/admin/import.html",
        {"upload": upload, "preview_rows": preview_rows},
    )


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
    clusters = StudentCluster.objects.select_related("cohort").all()

    ctx = {
        "staff_user": staff_user,
        "staff_role": staff_role,
        "back_url": back_url,
        "teacher_assignments": teacher_assignments,
        "moderator_assignments": moderator_assignments,
        "cohorts": cohorts,
        "clusters": clusters,
        "GROUP_ADMIN": GROUP_ADMIN,
        "GROUP_SUPER_ADMIN": GROUP_SUPER_ADMIN,
        "GROUP_MODERATOR": GROUP_MODERATOR,
        "GROUP_TEACHER": GROUP_TEACHER,
    }
    return render(request, "ui/pages/admin/staff_assignments.html", ctx)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_http_methods(["POST"])
def admin_moderator_assignment_create(request, user_id):
    staff_user = get_object_or_404(User, id=user_id)
    if GROUP_MODERATOR not in staff_user.groups.values_list("name", flat=True):
        messages.error(request, _("Target user is not a Moderator."))
        return redirect("accounts:admin_staff_assignments", user_id=staff_user.id)
    scope = request.POST.get("scope", "cohort")
    cohort_id = safe_int(request.POST.get("cohort"))
    cluster_id = safe_int(request.POST.get("cluster"))
    if scope == "cohort" and not cohort_id:
        messages.error(request, _("Cohort is required for cohort-wide assignment."))
        return redirect("accounts:admin_staff_assignments", user_id=staff_user.id)
    if scope == "cluster" and not cluster_id:
        messages.error(request, _("Cluster is required for cluster-specific assignment."))
        return redirect("accounts:admin_staff_assignments", user_id=staff_user.id)
    cluster = get_object_or_404(StudentCluster, id=cluster_id) if cluster_id else None
    cohort = cluster.cohort if cluster else get_object_or_404(Cohort, id=cohort_id)
    assignment = ModeratorAssignment.objects.create(
        moderator=staff_user,
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
        section=SECTION_ACCOUNTS,
        target_label=f"{staff_user} · {cohort}",
        subject_type="user",
        subject_id=str(staff_user.id),
        subject_label=str(staff_user),
        metadata={"moderator_id": str(staff_user.id), "cohort_id": str(cohort.id)},
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
        section=SECTION_ACCOUNTS,
        target_label=str(assignment),
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
        section=SECTION_ACCOUNTS,
        target_label=cluster.name,
        metadata={"cohort_id": str(cohort.id), "name": name},
    )
    messages.success(request, _("Cluster created."))
    return redirect(reverse("academics:admin_diploma_detail", kwargs={"diploma_id": cohort.diploma_id}))


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_http_methods(["POST"])
def admin_student_cluster_membership_add(request, cluster_id):
    cluster = get_object_or_404(StudentCluster, id=cluster_id)
    student_ids = [safe_int(sid) for sid in request.POST.getlist("students")]
    if not student_ids:
        messages.error(request, _("Select at least one student."))
        return redirect(
            reverse("academics:admin_diploma_detail", kwargs={"diploma_id": cluster.cohort.diploma_id})
        )
    if any(sid is None for sid in student_ids):
        messages.error(request, _("Invalid student selection."))
        return redirect(
            reverse("academics:admin_diploma_detail", kwargs={"diploma_id": cluster.cohort.diploma_id})
        )
    added = 0
    duplicate_count = 0
    outside_cohort_count = 0
    for sid in student_ids:
        profile = get_object_or_404(StudentProfile, id=sid)
        if not DiplomaEnrollment.objects.filter(
            student=profile,
            cohort=cluster.cohort,
            status=ENROLLMENT_STATUS_ACTIVE,
        ).exists():
            outside_cohort_count += 1
            continue
        if StudentClusterMembership.objects.filter(
            student=profile,
            cohort=cluster.cohort,
        ).exists():
            duplicate_count += 1
            continue
        try:
            StudentClusterMembership.objects.create(
                cluster=cluster,
                student=profile,
                assigned_by=request.user,
            )
        except IntegrityError:
            duplicate_count += 1
        else:
            added += 1
    record_audit_event(
        actor=request.user,
        action=ACTION_CLUSTER_MEMBERSHIP_ADDED,
        target_type="student_cluster_membership",
        target_id=str(cluster.id),
        section=SECTION_ACCOUNTS,
        target_label=cluster.name,
        metadata={"count": added, "cohort_id": str(cluster.cohort_id)},
    )
    msg = _("%(count)d student(s) added to cluster.") % {"count": added}
    if duplicate_count:
        messages.warning(
            request, _("%(count)d student(s) were already in a cluster.") % {"count": duplicate_count}
        )
    if outside_cohort_count:
        messages.warning(
            request,
            _("%(count)d student(s) are not enrolled in this cohort.") % {"count": outside_cohort_count},
        )
    messages.success(request, msg)
    if is_htmx(request):
        response = render(request, "ui/fragments/admin/_cluster_members.html", {"cluster": cluster})
        return hx_toast(response, msg, type="success")
    return redirect("academics:admin_diploma_detail", diploma_id=cluster.cohort.diploma_id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_http_methods(["POST"])
def admin_student_cluster_membership_remove(request, cluster_id, membership_id):
    membership = get_object_or_404(StudentClusterMembership, id=membership_id, cluster_id=cluster_id)
    cluster = membership.cluster
    student = membership.student
    membership.delete()
    record_audit_event(
        actor=request.user,
        action=ACTION_CLUSTER_MEMBERSHIP_REMOVED,
        target_type="student_cluster_membership",
        target_id=str(membership_id),
        section=SECTION_ACCOUNTS,
        target_label=cluster.name,
        subject_type="student",
        subject_id=str(student.pk),
        subject_label=str(student),
        metadata={"cluster_id": str(cluster_id), "student_id": str(student.pk)},
    )
    msg = _("Student removed from cluster.")
    messages.success(request, msg)
    if is_htmx(request):
        response = render(
            request, "ui/fragments/admin/_cluster_members.html", {"cluster": membership.cluster}
        )
        return hx_toast(response, msg, type="success")
    return redirect("academics:admin_diploma_detail", diploma_id=membership.cluster.cohort.diploma_id)


def _dashboard_url_name(user: User) -> str:
    if user.is_superuser or user.primary_role in (GROUP_SUPER_ADMIN, GROUP_ADMIN):
        return "accounts:dashboard_admin"
    if is_infrastructure_manager(user):
        return "accounts:admin_settings"
    role = user.primary_role
    if role == GROUP_MODERATOR:
        return "accounts:dashboard_moderator"
    if role == GROUP_REVIEWER:
        return "assessments:reviewer_dashboard"
    if role == GROUP_SECRETARY:
        return "accounts:dashboard_secretary"
    if role == GROUP_STUDENT:
        return "students:dashboard"
    return "core:landing"


@require_http_methods(["GET", "POST"])
def activate_account(request, uidb64, token):
    """Activate an account via the emailed link.

    GET renders a confirmation page (no state changes). POST performs the
    activation, dependent application state, and initial password setting in
    one transaction, then logs the user in.
    """
    pk = validate_activation_token(uidb64, token)
    if pk is None:
        messages.error(request, _("Activation link is invalid or expired."))
        return redirect("core:landing")
    if request.method == "GET":
        return render(
            request,
            "ui/pages/public/activation_confirm.html",
            {
                "uidb64": uidb64,
                "token": token,
            },
        )
    # POST — perform only the account activation mutation inside ONE transaction.
    form = ActivationConfirmForm(request.POST, user=User.objects.filter(pk=pk).first())
    if not form.is_valid():
        return render(
            request,
            "ui/pages/public/activation_confirm.html",
            {
                "uidb64": uidb64,
                "token": token,
                "form": form,
            },
            status=400,
        )
    with transaction.atomic():
        user = User.objects.select_for_update().filter(pk=pk).first()
        if not user:
            messages.error(request, _("Activation link is invalid or expired."))
            return redirect("core:landing")
        if user.email_activated_at is not None or user.is_active:
            # The link is not a perpetual passwordless login, so no session is
            # created. Application state remains unchanged for Admin review.
            messages.info(request, _("Account is already activated. Please log in."))
            return redirect("accounts:login")
        user.is_active = True
        user.email_activated_at = timezone.now()
        user.set_password(form.cleaned_data["new_password1"])
        user.save(update_fields=["is_active", "email_activated_at", "password", "updated_at"])
        handle_user_account_activated(user, actor=user)
        record_audit_event(
            actor=user,
            action=ACTION_USER_UPDATED,
            target_type="user",
            target_id=str(user.pk),
            section=SECTION_ACCOUNTS,
            target_label=str(user),
            subject_type="user",
            subject_id=str(user.pk),
            subject_label=str(user),
            metadata={"changed_fields": ["activation"], "activation": "self_service"},
        )
    auth_login(request, user)
    messages.success(request, _("Account activated successfully. Welcome!"))
    return redirect(_dashboard_url_name(user))


@require_http_methods(["GET", "POST"])
def resend_activation_email(request):
    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        user = User.objects.filter(email=email, is_active=False, email_activated_at__isnull=True).first()
        if user and _throttle_allowed(f"activation-resend:{email.casefold()}"):
            resent_count = CommEmailMessage.objects.filter(
                to_email=user.email,
                template_slug=TEMPLATE_ACTIVATION,
            ).count()
            send_activation_email(
                user,
                idempotency_key=f"activation:resend:{user.pk}:{resent_count}",
            )
        # The template check is global so the response never reveals whether the
        # address belongs to an inactive account.
        if EmailTemplate.objects.filter(
            slug=TEMPLATE_ACTIVATION,
            is_active=True,
            is_enabled_for_action=True,
        ).exists():
            messages.success(request, _("If an inactive account exists, a new activation email was queued."))
        else:
            messages.error(
                request, _("Activation emails are temporarily unavailable. Please try again later.")
            )
        return redirect("accounts:login")
    return render(request, "ui/pages/public/resend_activation.html")


@login_required
@require_http_methods(["POST"])
def send_my_activation_email(request):
    """Send a fresh activation link to the signed-in user's own email address.

    Email activation is deliberately not an admissions gate, so a signed-in
    student whose email was never activated can re-request the link from their
    own profile instead of relying on an Admin.
    """
    user = request.user
    if user.email_activated_at is not None or user.is_active:
        messages.info(request, _("Your email address is already activated."))
        return redirect("accounts:profile")
    if not _throttle_allowed(f"activation-self:{user.pk}"):
        messages.info(request, _("An activation email was sent recently. Please wait a moment."))
        return redirect("accounts:profile")
    sent_count = CommEmailMessage.objects.filter(
        to_email=user.email,
        template_slug=TEMPLATE_ACTIVATION,
    ).count()
    record = send_activation_email(
        user,
        sent_by=user,
        idempotency_key=f"activation:profile:{user.pk}:{sent_count}",
    )
    if record is None:
        messages.error(request, _("Activation emails are temporarily unavailable. Please try again later."))
    else:
        messages.success(request, _("A new activation link was sent to your email address."))
    return redirect("accounts:profile")
