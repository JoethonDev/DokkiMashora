"""Public registration and admin review views."""

from __future__ import annotations

from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from apps.accounts.constants import GROUP_STUDENT
from apps.audit.constants import (
    ACTION_APPLICATION_ACTIVATED,
    ACTION_APPLICATION_APPROVED,
    ACTION_APPLICATION_REJECTED,
    ACTION_APPLICATION_STATUS_CHANGE,
)
from apps.audit.services import record_audit_event
from apps.core.permissions import is_admin, is_moderator
from apps.students.models import StudentProfile

from .constants import (
    APP_STATUS_APPROVED,
    APP_STATUS_PENDING,
    APP_STATUS_UNDER_REVIEW,
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_INVITED,
    PROGRAM_APP_STATUS_SUBMITTED,
)
from .constants import (
    PROGRAM_APP_STATUS_UNDER_REVIEW as PROGRAM_UNDER_REVIEW,
)
from .forms import ApplicationDecisionForm, RegistrationForm
from .models import Application, ProgramApplication
from .services import RegistrationData, submit_registration

PUBLIC_REGISTRATION_SESSION_KEY = "registration_email"


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
    applications = Application.objects.select_related("student__user", "reviewed_by").order_by("-created_at")
    if status_filter:
        applications = applications.filter(status=status_filter)
    context = {
        "applications": applications,
        "status_filter": status_filter,
        "status_choices": Application._meta.get_field("status").choices,
    }
    if request.headers.get("HX-Target") == "applications-list":
        return render(request, "applications/partials/_list_table.html", context)
    return render(request, "applications/admin_list.html", context)


@require_http_methods(["GET", "POST"])
@login_required
def application_detail(request: HttpRequest, application_id: int) -> HttpResponse:
    """Admin detail/edit view with approve/reject/activate actions."""
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
            application.mark_under_review(request.user)
            record_audit_event(
                actor=request.user,
                action=ACTION_APPLICATION_STATUS_CHANGE,
                target_type="application",
                target_id=str(application.id),
                metadata={"status": APP_STATUS_UNDER_REVIEW},
            )
            messages.info(request, _("Application marked as under review."))
            return redirect("applications:admin_detail", application_id=application.id)
        if action == "approve" and application.status in {APP_STATUS_PENDING, APP_STATUS_UNDER_REVIEW}:
            if not decision_form.is_valid():
                return _render_detail(request, application, decision_form)
            application.approve(request.user, notes=decision_form.cleaned_data.get("admin_notes", ""))
            record_audit_event(
                actor=request.user,
                action=ACTION_APPLICATION_APPROVED,
                target_type="application",
                target_id=str(application.id),
                metadata={"student_id": application.student_id},
            )
            messages.success(request, _("Application approved."))
            return redirect("applications:admin_detail", application_id=application.id)
        if action == "reject" and application.status in {APP_STATUS_PENDING, APP_STATUS_UNDER_REVIEW}:
            reason = (request.POST.get("rejection_reason") or "").strip()
            if not reason:
                decision_form.add_error("rejection_reason", _("Rejection reason is required."))
                return _render_detail(request, application, decision_form)
            admin_notes = (request.POST.get("admin_notes") or "").strip()
            application.reject(request.user, reason=reason, notes=admin_notes)
            record_audit_event(
                actor=request.user,
                action=ACTION_APPLICATION_REJECTED,
                target_type="application",
                target_id=str(application.id),
                metadata={"reason": reason, "student_id": application.student_id},
            )
            messages.warning(request, _("Application rejected."))
            return redirect("applications:admin_detail", application_id=application.id)
        if action == "activate" and application.status == APP_STATUS_APPROVED:
            application.activate()
            _activate_student(application.student)
            record_audit_event(
                actor=request.user,
                action=ACTION_APPLICATION_ACTIVATED,
                target_type="application",
                target_id=str(application.id),
                metadata={"student_id": application.student_id, "user_id": application.student.user_id},
            )
            messages.success(request, _("Application activated."))
            return redirect("applications:admin_detail", application_id=application.id)
        messages.error(request, _("Action not allowed for the current status."))
    return _render_detail(request, application, decision_form)


def _render_detail(
    request: HttpRequest, application: Application, form: ApplicationDecisionForm
) -> HttpResponse:
    return render(
        request,
        "applications/admin_detail.html",
        {"application": application, "form": form},
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
    applications = ProgramApplication.objects.select_related(
        "student__user", "reviewed_by", "cohort__diploma", "course"
    ).order_by("-created_at")
    if status_filter:
        applications = applications.filter(status=status_filter)
    if program_type_filter:
        applications = applications.filter(program_type=program_type_filter)
    context = {
        "applications": applications,
        "status_filter": status_filter,
        "program_type_filter": program_type_filter,
        "status_choices": ProgramApplication._meta.get_field("status").choices,
        "program_type_choices": ProgramApplication._meta.get_field("program_type").choices,
    }
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
            application.mark_under_review(request.user)
            record_audit_event(
                actor=request.user,
                action=ACTION_APPLICATION_STATUS_CHANGE,
                target_type="program_application",
                target_id=str(application.id),
                metadata={"status": PROGRAM_UNDER_REVIEW},
            )
            messages.info(request, _("Application marked as under review."))
            return redirect("applications:program_detail", application_id=application.id)
        if action == "invite" and application.status in {PROGRAM_APP_STATUS_SUBMITTED, PROGRAM_UNDER_REVIEW}:
            application.mark_invited(request.user)
            record_audit_event(
                actor=request.user,
                action=ACTION_APPLICATION_STATUS_CHANGE,
                target_type="program_application",
                target_id=str(application.id),
                metadata={"status": PROGRAM_APP_STATUS_INVITED},
            )
            messages.success(request, _("Applicant invited to entrance assessment."))
            return redirect("applications:program_detail", application_id=application.id)
        if action == "accept" and application.status in {PROGRAM_APP_STATUS_SUBMITTED, PROGRAM_UNDER_REVIEW}:
            notes = (request.POST.get("admin_notes") or "").strip()
            application.accept(request.user, notes=notes)
            record_audit_event(
                actor=request.user,
                action=ACTION_APPLICATION_APPROVED,
                target_type="program_application",
                target_id=str(application.id),
                metadata={"student_id": application.student_id},
            )
            messages.success(request, _("Application accepted."))
            return redirect("applications:program_detail", application_id=application.id)
        if action == "reject":
            reason = (request.POST.get("rejection_reason") or "").strip()
            if not reason:
                messages.error(request, _("Rejection reason is required."))
                return redirect("applications:program_detail", application_id=application.id)
            admin_notes = (request.POST.get("admin_notes") or "").strip()
            application.reject(request.user, reason=reason, notes=admin_notes)
            record_audit_event(
                actor=request.user,
                action=ACTION_APPLICATION_REJECTED,
                target_type="program_application",
                target_id=str(application.id),
                metadata={"reason": reason, "student_id": application.student_id},
            )
            messages.warning(request, _("Application rejected."))
            return redirect("applications:program_detail", application_id=application.id)
        if action == "activate" and application.status == PROGRAM_APP_STATUS_ACCEPTED:
            application.activate()
            _activate_student(application.student)
            record_audit_event(
                actor=request.user,
                action=ACTION_APPLICATION_ACTIVATED,
                target_type="program_application",
                target_id=str(application.id),
                metadata={
                    "student_id": application.student_id,
                    "user_id": application.student.user_id,
                },
            )
            messages.success(request, _("Application activated."))
            return redirect("applications:program_detail", application_id=application.id)
        messages.error(request, _("Action not allowed for the current status."))
    return render(
        request,
        "applications/admin_program_detail.html",
        {"application": application},
    )


# ---------------------------------------------------------------------------
# Dashboard reapply (creates child ProgramApplication, not root Application)
# ---------------------------------------------------------------------------


@login_required
@require_http_methods(["GET", "POST"])
def dashboard_apply(request: HttpRequest) -> HttpResponse:
    """Dashboard apply flow — creates a child ProgramApplication."""
    from apps.academics.models import Cohort, Course

    profile = _get_profile_or_404(request.user)

    from .services import submit_program_application

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
        from django.http import Http404
        raise Http404("No student profile.")
    return user.student_profile


def _activate_student(profile: StudentProfile) -> None:
    group, _ = Group.objects.get_or_create(name=GROUP_STUDENT)
    profile.user.groups.add(group)
    profile.user.is_active = True
    profile.user.save(update_fields=["is_active", "updated_at"])
