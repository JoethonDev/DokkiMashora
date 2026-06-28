"""Public registration and admin review views."""

from __future__ import annotations

from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db import models
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from apps.academics.models import Cohort, Course, Diploma
from apps.core.htmx import is_htmx
from apps.core.pagination import paginate
from apps.core.permissions import is_admin, is_moderator
from apps.entrance_exams.services import assign_exam_for_application

from .constants import (
    APP_STATUS_ACTIVATED as APP_TERMINAL,
)
from .constants import (
    APP_STATUS_APPROVED,
    APP_STATUS_PENDING,
    APP_STATUS_UNDER_REVIEW,
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_SUBMITTED,
)
from .constants import (
    PROGRAM_APP_STATUS_ACTIVATED as PROG_TERMINAL,
)
from .constants import (
    PROGRAM_APP_STATUS_UNDER_REVIEW as PROGRAM_UNDER_REVIEW,
)
from .forms import ApplicationDecisionForm, RegistrationForm
from .models import REVERTABLE_ACTIONS, Application, ApplicationDecision, ProgramApplication
from .services import (
    RegistrationData,
    activate_application,
    activate_program_application,
    approve_application,
    approve_program_application,
    invite_program_application,
    mark_review_application,
    mark_review_program_application,
    reject_application,
    reject_program_application,
    revert_application_decision,
    revert_program_application_decision,
    submit_program_application,
    submit_registration,
)

PUBLIC_REGISTRATION_SESSION_KEY = "registration_email"


@require_http_methods(["GET"])
def program_diplomas(request: HttpRequest) -> HttpResponse:
    """Return diploma <option>s for HTMX."""
    now = timezone.now()
    diploma_ids = (
        Cohort.objects.filter(is_active=True, entrance_exams__close_at__gte=now)
        .values_list("diploma_id", flat=True)
        .distinct()
    )
    diplomas = Diploma.objects.filter(pk__in=diploma_ids, is_active=True).order_by("title")
    options = [(str(d.pk), d.title) for d in diplomas]
    return render(request, "applications/partials/_program_options.html", {"options": options, "placeholder": "Select a diploma"})


@require_http_methods(["GET"])
def program_targets(request: HttpRequest) -> HttpResponse:
    """Return course <option>s for HTMX."""
    courses = Course.objects.filter(is_active=True, is_standalone=True).order_by("title")
    options = [(f"course_{c.pk}", c.title) for c in courses]
    return render(request, "applications/partials/_program_options.html", {"options": options, "placeholder": "Select a course"})


@require_http_methods(["GET", "POST"])
def register(request: HttpRequest) -> HttpResponse:
    """Public registration form. Authenticated users go to their dashboard."""
    if request.user.is_authenticated:
        return redirect("accounts:post_login")
    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                program_data = form.get_program_data()
                data = RegistrationData(
                    email=form.cleaned_data["email"],
                    full_name=form.cleaned_data["full_name"],
                    date_of_birth=form.cleaned_data["date_of_birth"],
                    gender=form.cleaned_data["gender"],
                    address=form.cleaned_data["address"],
                    phone=form.cleaned_data["phone"],
                    national_id=form.cleaned_data["national_id"],
                    education=form.cleaned_data["education"],
                    specialization=form.cleaned_data.get("specialization", ""),
                    employer=form.cleaned_data.get("employer", ""),
                    current_job=form.cleaned_data.get("current_job", ""),
                    church=form.cleaned_data.get("church", ""),
                    priest_name=form.cleaned_data.get("priest_name", ""),
                    priest_contact=form.cleaned_data.get("priest_contact", ""),
                    age=(date.today() - form.cleaned_data["date_of_birth"]).days // 365,
                    program_type=program_data["program_type"],
                    cohort_id=program_data["cohort_id"],
                    course_id=program_data["course_id"],
                )
                _profile, _root_application, _program_application = submit_registration(data)
            request.session[PUBLIC_REGISTRATION_SESSION_KEY] = form.cleaned_data["email"]
            messages.success(request, _("Registration received. We will contact you shortly."))
            return redirect("applications:registration_success")
    else:
        form = RegistrationForm()
    return render(request, "applications/register.html", {"form": form})


@require_http_methods(["GET"])
def registration_success(request: HttpRequest) -> HttpResponse:
    email = request.session.pop(PUBLIC_REGISTRATION_SESSION_KEY, None)
    return render(request, "applications/registration_success.html", {"email": email})


# ---------------------------------------------------------------------------
# Root Application admin (legacy, kept for backward compatibility)
# ---------------------------------------------------------------------------


@require_http_methods(["GET"])
@login_required
def application_list(request: HttpRequest) -> HttpResponse:
    """Admin list of all root applications, with status filter."""
    if not (is_admin(request.user) or is_moderator(request.user)):
        raise PermissionDenied
    status_filter = request.GET.get("status", "")
    q = request.GET.get("q", "")
    applications = Application.objects.select_related("student__user", "reviewed_by").order_by("-created_at")
    if q:
        applications = applications.filter(
            Q(student__full_name__icontains=q) | Q(student__user__email__icontains=q)
        )
    if status_filter:
        applications = applications.filter(status=status_filter)
    filters = []
    if status_filter is not None:
        filters.append({
            "name": "status",
            "label": _("Status"),
            "value": status_filter,
            "options": [{"value": v, "label": _(label_text), "selected": status_filter == v}
                        for v, label_text in Application._meta.get_field("status").choices],
        })
    page_obj, pagination_qs = paginate(request, applications)
    context = {
        "page_obj": page_obj,
        "pagination_qs": pagination_qs,
        "search_value": q,
        "filters": filters,
    }
    if request.headers.get("HX-Target") == "applications-list":
        return render(request, "applications/partials/_list_table.html", context)
    return render(request, "applications/admin_list.html", context)


@require_http_methods(["GET", "POST"])
@login_required
def application_detail(request: HttpRequest, application_id: int) -> HttpResponse:
    """Admin detail/edit view with approve/reject/activate/revert actions."""
    if not (is_admin(request.user) or is_moderator(request.user)):
        raise PermissionDenied
    application = get_object_or_404(
        Application.objects.select_related("student__user", "reviewed_by"),
        pk=application_id,
    )
    decision_form = ApplicationDecisionForm(instance=application)
    if request.method == "POST":
        action = request.POST.get("action")
        decision_form = ApplicationDecisionForm(request.POST, instance=application)
        if action == "mark_review" and application.status == APP_STATUS_PENDING:
            mark_review_application(application, request.user)
            messages.info(request, _("Application marked as under review."))
            return redirect("applications:admin_detail", application_id=application.id)
        if action == "approve" and application.status in {APP_STATUS_PENDING, APP_STATUS_UNDER_REVIEW}:
            if not decision_form.is_valid():
                return _render_detail(request, application, decision_form)
            approve_application(application, request.user, notes=decision_form.cleaned_data.get("admin_notes", ""))
            messages.success(request, _("Application approved."))
            return redirect("applications:admin_detail", application_id=application.id)
        if action == "reject" and application.status in {APP_STATUS_PENDING, APP_STATUS_UNDER_REVIEW}:
            reason = (request.POST.get("rejection_reason") or "").strip()
            if not reason:
                decision_form.add_error("rejection_reason", _("Rejection reason is required."))
                return _render_detail(request, application, decision_form)
            admin_notes = (request.POST.get("admin_notes") or "").strip()
            reject_application(application, request.user, reason=reason, notes=admin_notes)
            messages.warning(request, _("Application rejected."))
            return redirect("applications:admin_detail", application_id=application.id)
        if action == "activate" and application.status == APP_STATUS_APPROVED:
            activate_application(application, request.user)
            messages.success(request, _("Application activated."))
            return redirect("applications:admin_detail", application_id=application.id)
        if action == "revert" and application.status != APP_TERMINAL:
            reason = (request.POST.get("revert_reason") or "").strip()
            if not reason:
                messages.error(request, _("Please provide a reason for reverting."))
                return redirect("applications:admin_detail", application_id=application.id)
            try:
                revert_application_decision(application, request.user, reason=reason)
                messages.success(request, _("Decision reverted."))
            except ValueError as e:
                messages.error(request, str(e))
            return redirect("applications:admin_detail", application_id=application.id)
        messages.error(request, _("Action not allowed for the current status."))
    return _render_detail(request, application, decision_form)


def _render_detail(
    request: HttpRequest, application: Application, form: ApplicationDecisionForm
) -> HttpResponse:
    base_qs = ApplicationDecision.objects.filter(
        target_type="application", target_id=str(application.pk)
    )
    can_revert = (
        application.status != APP_TERMINAL
        and base_qs.filter(action__in=REVERTABLE_ACTIONS).exists()
    )
    return render(
        request,
        "applications/admin_detail.html",
        {"application": application, "form": form, "decisions": base_qs[:20], "can_revert": can_revert},
    )


# ---------------------------------------------------------------------------
# Program Application admin (new child-request workflow)
# ---------------------------------------------------------------------------


@require_http_methods(["GET"])
@login_required
def program_application_list(request: HttpRequest) -> HttpResponse:
    """Admin list of program applications/requests, with status filter."""
    if not (is_admin(request.user) or is_moderator(request.user)):
        raise PermissionDenied
    status_filter = request.GET.get("status", "")
    program_type_filter = request.GET.get("program_type", "")
    q = request.GET.get("q", "")
    applications = ProgramApplication.objects.select_related(
        "student__user", "reviewed_by", "cohort__diploma", "course"
    ).order_by("-created_at")
    if q:
        applications = applications.filter(
            Q(student__full_name__icontains=q) | Q(student__user__email__icontains=q)
        )
    if status_filter:
        applications = applications.filter(status=status_filter)
    if program_type_filter:
        applications = applications.filter(program_type=program_type_filter)
    filters = [
        {
            "name": "status",
            "label": _("Status"),
            "value": status_filter,
            "options": [{"value": v, "label": _(label_text), "selected": status_filter == v}
                        for v, label_text in ProgramApplication._meta.get_field("status").choices],
        },
        {
            "name": "program_type",
            "label": _("Type"),
            "value": program_type_filter,
            "options": [{"value": v, "label": _(label_text), "selected": program_type_filter == v}
                        for v, label_text in ProgramApplication._meta.get_field("program_type").choices],
        },
    ]
    page_obj, pagination_qs = paginate(request, applications)
    context = {
        "page_obj": page_obj,
        "pagination_qs": pagination_qs,
        "search_value": q,
        "filters": filters,
    }
    if is_htmx(request):
        return render(request, "applications/partials/_program_list_table.html", context)
    return render(request, "applications/admin_program_list.html", context)


@require_http_methods(["GET", "POST"])
@login_required
def program_application_detail(request: HttpRequest, application_id: int) -> HttpResponse:
    """Admin detail/edit view for a ProgramApplication with workflow actions."""
    if not (is_admin(request.user) or is_moderator(request.user)):
        raise PermissionDenied
    application = get_object_or_404(
        ProgramApplication.objects.select_related(
            "student__user", "reviewed_by", "cohort__diploma", "course"
        ),
        pk=application_id,
    )
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "mark_review" and application.status == PROGRAM_APP_STATUS_SUBMITTED:
            mark_review_program_application(application, request.user)
            messages.info(request, _("Application marked as under review."))
            return redirect("applications:program_detail", application_id=application.id)
        if action == "invite" and application.status in {PROGRAM_APP_STATUS_SUBMITTED, PROGRAM_UNDER_REVIEW}:
            invite_program_application(application, request.user)
            if assign_exam_for_application(application):
                messages.success(request, _("Applicant invited to entrance assessment."))
            else:
                messages.success(
                    request,
                    _("Applicant invited. No active entrance exam exists for this cohort — please create one."),
                )
            return redirect("applications:program_detail", application_id=application.id)
        if action == "accept" and application.status in {PROGRAM_APP_STATUS_SUBMITTED, PROGRAM_UNDER_REVIEW}:
            notes = (request.POST.get("admin_notes") or "").strip()
            approve_program_application(application, request.user, notes=notes)
            messages.success(request, _("Application accepted."))
            return redirect("applications:program_detail", application_id=application.id)
        if action == "reject":
            reason = (request.POST.get("rejection_reason") or "").strip()
            if not reason:
                messages.error(request, _("Rejection reason is required."))
                return redirect("applications:program_detail", application_id=application.id)
            admin_notes = (request.POST.get("admin_notes") or "").strip()
            reject_program_application(application, request.user, reason=reason, notes=admin_notes)
            messages.warning(request, _("Application rejected."))
            return redirect("applications:program_detail", application_id=application.id)
        if action == "activate" and application.status == PROGRAM_APP_STATUS_ACCEPTED:
            activate_program_application(application, request.user)
            messages.success(request, _("Application activated."))
            return redirect("applications:program_detail", application_id=application.id)
        if action == "revert" and application.status != PROG_TERMINAL:
            reason = (request.POST.get("revert_reason") or "").strip()
            if not reason:
                messages.error(request, _("Please provide a reason for reverting."))
                return redirect("applications:program_detail", application_id=application.id)
            try:
                revert_program_application_decision(application, request.user, reason=reason)
                messages.success(request, _("Decision reverted."))
            except ValueError as e:
                messages.error(request, str(e))
            return redirect("applications:program_detail", application_id=application.id)
        messages.error(request, _("Action not allowed for the current status."))
    base_qs = ApplicationDecision.objects.filter(
        target_type="program_application", target_id=str(application.pk)
    )
    can_revert = (
        application.status != PROG_TERMINAL
        and base_qs.filter(action__in=REVERTABLE_ACTIONS).exists()
    )
    return render(
        request,
        "applications/admin_program_detail.html",
        {"application": application, "decisions": base_qs[:20], "can_revert": can_revert},
    )


# ---------------------------------------------------------------------------
# Dashboard reapply (creates child ProgramApplication, not root Application)
# ---------------------------------------------------------------------------


@login_required
@require_http_methods(["GET", "POST"])
def dashboard_apply(request: HttpRequest) -> HttpResponse:
    """Dashboard apply flow — creates a child ProgramApplication."""
    profile = _get_profile_or_404(request.user)

    cohorts = list(Cohort.objects.filter(is_active=True).select_related("diploma").order_by("-start_date"))
    courses = list(Course.objects.filter(is_active=True).order_by("title"))

    if request.method == "POST":
        program_type = request.POST.get("program_type")
        target_id = request.POST.get("target_id")
        if not program_type or not target_id:
            messages.error(request, _("Please select a program."))
            return redirect("applications:dashboard_apply")
        try:
            with transaction.atomic():
                if program_type == "diploma":
                    cohort = Cohort.objects.get(pk=target_id)
                    submit_program_application(profile, program_type="diploma", cohort=cohort)
                elif program_type == "course":
                    course = Course.objects.get(pk=target_id)
                    submit_program_application(profile, program_type="course", course=course)
                else:
                    messages.error(request, _("Invalid program type."))
                    return redirect("applications:dashboard_apply")
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("applications:dashboard_apply")
        messages.success(request, _("Your application has been submitted."))
        return redirect("students:application_history")
    return render(request, "applications/dashboard_apply.html", {
        "cohorts": cohorts,
        "courses": courses,
    })


def _get_profile_or_404(user):
    if not hasattr(user, "student_profile"):
        raise Http404(_("No student profile."))
    return user.student_profile



