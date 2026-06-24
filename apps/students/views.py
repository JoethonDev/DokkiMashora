"""Student profile views -- dashboard, details, history sections, reapply."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from apps.assessments.constants import (
    GRADE_VISIBILITY_HIDDEN,
    GRADE_VISIBILITY_RELEASED,
    GRADE_VISIBILITY_UNDER_REVIEW,
)
from apps.entrance_exams.constants import ATTEMPT_STATUS_GRADED

from .forms import StudentProfileUpdateForm
from .policies import EDITABLE_FIELDS, RESTRICTED_FIELDS, get_field_label
from .selectors import get_dashboard_data
from .services import check_reapply_eligibility, submit_reapply_application

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_profile_or_404(user):
    """Return the StudentProfile for the logged-in user, or 404."""
    if not hasattr(user, "student_profile"):
        from django.http import Http404
        raise Http404(_("No student profile."))
    return user.student_profile


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    """Student profile dashboard -- aggregation overview."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile)
    return render(request, "students/profile/dashboard.html", data)


# ---------------------------------------------------------------------------
# Profile details
# ---------------------------------------------------------------------------


@login_required
def details(request: HttpRequest) -> HttpResponse:
    """View-only student profile details."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile, sections=["identity"])
    field_labels = {f: get_field_label(f) for f in EDITABLE_FIELDS + RESTRICTED_FIELDS}
    data["editable_fields"] = EDITABLE_FIELDS
    data["restricted_fields"] = RESTRICTED_FIELDS
    data["field_labels"] = field_labels
    return render(request, "students/profile/details.html", data)


# ---------------------------------------------------------------------------
# Profile edit
# ---------------------------------------------------------------------------


@login_required
@require_http_methods(["GET", "POST"])
def edit_profile(request: HttpRequest) -> HttpResponse:
    """Edit allowed student profile fields."""
    profile = _get_profile_or_404(request.user)
    if request.method == "POST":
        form = StudentProfileUpdateForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, _("Profile updated."))
            return redirect("students:details")
    else:
        form = StudentProfileUpdateForm(instance=profile)
    return render(request, "students/profile/edit.html", {"form": form})


# ---------------------------------------------------------------------------
# Application history
# ---------------------------------------------------------------------------


@login_required
def application_history(request: HttpRequest) -> HttpResponse:
    """Show all applications submitted by the student."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile, sections=["applications", "reapply"])
    return render(request, "students/profile/applications.html", data)


# ---------------------------------------------------------------------------
# Enrollment history
# ---------------------------------------------------------------------------


@login_required
def enrollment_history(request: HttpRequest) -> HttpResponse:
    """Show all diploma and course enrollments."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile, sections=["diploma_enrollments", "course_enrollments"])
    return render(request, "students/profile/enrollments.html", data)


# ---------------------------------------------------------------------------
# Access list (active + expired/restricted)
# ---------------------------------------------------------------------------


@login_required
def access_list(request: HttpRequest) -> HttpResponse:
    """Show active and expired/restricted access."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile, sections=["active_courses", "expired_courses"])
    return render(request, "students/profile/access.html", data)


# ---------------------------------------------------------------------------
# Entrance exam results
# ---------------------------------------------------------------------------


@login_required
def exam_results(request: HttpRequest) -> HttpResponse:
    """Show entrance exam assignments and results."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile, sections=["exam_assignments"])
    data["attempt_status_graded"] = ATTEMPT_STATUS_GRADED
    return render(request, "students/profile/exams.html", data)


# ---------------------------------------------------------------------------
# Assessment history
# ---------------------------------------------------------------------------


@login_required
def assessment_history(request: HttpRequest) -> HttpResponse:
    """Show normal assessment attempts, grades, feedback."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile, sections=["assessment_attempts"])
    data["grade_visibility_hidden"] = GRADE_VISIBILITY_HIDDEN
    data["grade_visibility_released"] = GRADE_VISIBILITY_RELEASED
    data["grade_visibility_under_review"] = GRADE_VISIBILITY_UNDER_REVIEW
    return render(request, "students/profile/assessments.html", data)


# ---------------------------------------------------------------------------
# Homework submissions
# ---------------------------------------------------------------------------


@login_required
def homework_list(request: HttpRequest) -> HttpResponse:
    """Show homework submissions."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile, sections=["homework_submissions"])
    return render(request, "students/profile/homework.html", data)


# ---------------------------------------------------------------------------
# Progression history
# ---------------------------------------------------------------------------


@login_required
def progression_history(request: HttpRequest) -> HttpResponse:
    """Show academic lifecycle -- semesters, promotions, completions."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile, sections=[
        "semester_progress", "promotion_decisions", "completions", "eligibilities",
    ])
    return render(request, "students/profile/progression.html", data)


# ---------------------------------------------------------------------------
# Certificates
# ---------------------------------------------------------------------------


@login_required
def certificate_list(request: HttpRequest) -> HttpResponse:
    """Show available certificates."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile, sections=["certificates"])
    return render(request, "students/profile/certificates.html", data)


# ---------------------------------------------------------------------------
# Payment status (placeholder)
# ---------------------------------------------------------------------------


@login_required
def payment_status(request: HttpRequest) -> HttpResponse:
    """Show payment status -- placeholder until Phase 8."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile, sections=["payments"])
    return render(request, "students/profile/payments.html", data)


# ---------------------------------------------------------------------------
# Notification history
# ---------------------------------------------------------------------------


@login_required
def notification_history(request: HttpRequest) -> HttpResponse:
    """Show email/notification history."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile, sections=["notifications"])
    return render(request, "students/profile/notifications.html", data)


# ---------------------------------------------------------------------------
# Reapply
# ---------------------------------------------------------------------------


@login_required
@require_http_methods(["GET", "POST"])
def reapply(request: HttpRequest) -> HttpResponse:
    """Reapply flow -- create a new ProgramApplication if eligible."""
    from apps.academics.models import Cohort, Course

    profile = _get_profile_or_404(request.user)
    eligibility = check_reapply_eligibility(profile)
    cohorts = list(Cohort.objects.filter(is_active=True).select_related("diploma").order_by("-start_date"))
    courses = list(Course.objects.filter(is_active=True).order_by("title"))

    if request.method == "POST":
        program_type = request.POST.get("program_type")
        target_id = request.POST.get("target_id")
        if not program_type or not target_id:
            messages.error(request, _("Please select a program."))
            return redirect("students:reapply")

        if program_type == "diploma":
            cohort = next((c for c in cohorts if str(c.pk) == target_id), None)
            if not cohort:
                messages.error(request, _("Invalid cohort selected."))
                return redirect("students:reapply")
            cohort_eligibility = check_reapply_eligibility(profile, cohort=cohort)
            if not cohort_eligibility["allowed"]:
                messages.error(request, cohort_eligibility["reason"] or _("You are not eligible to reapply."))
                return redirect("students:reapply")
            try:
                submit_reapply_application(profile, cohort=cohort)
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect("students:reapply")
        elif program_type == "course":
            course = next((c for c in courses if str(c.pk) == target_id), None)
            if not course:
                messages.error(request, _("Invalid course selected."))
                return redirect("students:reapply")
            course_eligibility = check_reapply_eligibility(profile, course=course)
            if not course_eligibility["allowed"]:
                messages.error(request, course_eligibility["reason"] or _("You are not eligible to reapply."))
                return redirect("students:reapply")
            try:
                submit_reapply_application(profile, course=course)
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect("students:reapply")
        else:
            messages.error(request, _("Invalid program type."))
            return redirect("students:reapply")

        messages.success(request, _("Your application has been submitted."))
        return redirect("students:application_history")

    return render(request, "students/profile/reapply.html", {
        "eligibility": eligibility,
        "cohorts": cohorts,
        "courses": courses,
    })
