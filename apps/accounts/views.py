"""Views for authentication, dashboards, and profile."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from .constants import (
    GROUP_ADMIN,
    GROUP_MODERATOR,
    GROUP_STUDENT,
    GROUP_SUPER_ADMIN,
    GROUP_TEACHER,
)
from .forms import EmailAuthenticationForm, ProfileForm
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
    from apps.applications.constants import APP_STATUS_PENDING, APP_STATUS_UNDER_REVIEW
    from apps.applications.models import Application
    from apps.exams.constants import ASSIGNMENT_STATUS_COMPLETED
    from apps.exams.models import Assignment, Exam

    pending_count = Application.objects.filter(
        status__in=[APP_STATUS_PENDING, APP_STATUS_UNDER_REVIEW]
    ).count()
    active_exams = Exam.objects.filter(is_active=True).count()
    pending_grading = Assignment.objects.filter(
        status=ASSIGNMENT_STATUS_COMPLETED,
    ).count()
    context = _dashboard_context(request.user)
    context["pending_applications_count"] = pending_count
    context["active_exams_count"] = active_exams
    context["pending_grading_count"] = pending_grading
    return render(request, "accounts/dashboards/admin.html", context)


@login_required
def moderator_dashboard(request: HttpRequest) -> HttpResponse:
    from apps.applications.constants import APP_STATUS_PENDING, APP_STATUS_UNDER_REVIEW
    from apps.applications.models import Application

    pending_count = Application.objects.filter(
        status__in=[APP_STATUS_PENDING, APP_STATUS_UNDER_REVIEW]
    ).count()
    context = _dashboard_context(request.user)
    context["pending_applications_count"] = pending_count
    return render(request, "accounts/dashboards/moderator.html", context)


@login_required
def teacher_dashboard(request: HttpRequest) -> HttpResponse:
    return render(request, "accounts/dashboards/teacher.html", _dashboard_context(request.user))


@login_required
def student_dashboard(request: HttpRequest) -> HttpResponse:
    return render(request, "accounts/dashboards/student.html", _dashboard_context(request.user))


# --- Helpers ----------------------------------------------------------------
def _dashboard_context(user: User) -> dict:
    return {
        "user_role": user.primary_role or _("Super Admin"),
        "show_role_label": True,
    }


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
