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
from apps.core.permissions import is_admin, is_moderator
from apps.students.models import StudentProfile

from .constants import (
    APP_STATUS_APPROVED,
    APP_STATUS_PENDING,
    APP_STATUS_UNDER_REVIEW,
)
from .forms import ApplicationDecisionForm, RegistrationForm
from .models import Application
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
                )
                _profile, _application = submit_registration(data)
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


@require_http_methods(["GET"])
@login_required
def application_list(request: HttpRequest) -> HttpResponse:
    """Admin list of all applications, with status filter."""
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
            messages.info(request, _("Application marked as under review."))
            return redirect("applications:admin_detail", application_id=application.id)
        if action == "approve" and application.status in {APP_STATUS_PENDING, APP_STATUS_UNDER_REVIEW}:
            if not decision_form.is_valid():
                return _render_detail(request, application, decision_form)
            application.approve(request.user, notes=decision_form.cleaned_data.get("admin_notes", ""))
            messages.success(request, _("Application approved."))
            return redirect("applications:admin_detail", application_id=application.id)
        if action == "reject" and application.status in {APP_STATUS_PENDING, APP_STATUS_UNDER_REVIEW}:
            reason = (request.POST.get("rejection_reason") or "").strip()
            if not reason:
                decision_form.add_error("rejection_reason", _("Rejection reason is required."))
                return _render_detail(request, application, decision_form)
            admin_notes = (request.POST.get("admin_notes") or "").strip()
            application.reject(request.user, reason=reason, notes=admin_notes)
            messages.warning(request, _("Application rejected."))
            return redirect("applications:admin_detail", application_id=application.id)
        if action == "activate" and application.status == APP_STATUS_APPROVED:
            application.activate()
            _activate_student(application.student)
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


def _activate_student(profile: StudentProfile) -> None:
    group, _ = Group.objects.get_or_create(name=GROUP_STUDENT)
    profile.user.groups.add(group)
    profile.user.is_active = True
    profile.user.save(update_fields=["is_active", "updated_at"])
