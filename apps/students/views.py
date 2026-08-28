"""Student profile views -- dashboard, details, history sections, apply."""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from apps.academics.models import Cohort, Course
from apps.applications.constants import DELIVERY_MODE_CHOICES, DELIVERY_MODE_ONLINE
from apps.applications.services import is_apply_target_open
from apps.core.htmx import hx_toast, is_htmx
from apps.core.pagination import paginate
from apps.core.permissions import student_required

from .forms import StudentProfileUpdateForm
from .policies import EDITABLE_FIELDS, RESTRICTED_FIELDS, get_field_label
from .selectors import get_application_row, get_dashboard_data
from .services import check_apply_eligibility, get_open_apply_targets, submit_apply_application

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_profile_or_404(user):
    """Return the StudentProfile for the logged-in user, or 404."""
    if not hasattr(user, "student_profile"):
        raise Http404(_("No student profile."))
    return user.student_profile


def _render_htmx(request, template_name, context):
    """Render the replaceable student content boundary."""
    name = template_name.rsplit("/", 1)[-1].replace(".html", "")
    return render(request, f"ui/fragments/student/_{name}.html", context)


def _render_profile(request, template_name, context):
    """Full page or HTMX fragment depending on request."""
    page_template = template_name
    if is_htmx(request):
        return _render_htmx(request, page_template, context)
    return render(request, page_template, context)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@student_required
def dashboard(request: HttpRequest) -> HttpResponse:
    """Student profile dashboard -- aggregation overview."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile)
    return _render_profile(request, "ui/pages/student/dashboard.html", data)


# ---------------------------------------------------------------------------
# Profile details
# ---------------------------------------------------------------------------


@student_required
def details(request: HttpRequest) -> HttpResponse:
    """View-only student profile details."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile, sections=["identity"])
    field_labels = {f: get_field_label(f) for f in EDITABLE_FIELDS + RESTRICTED_FIELDS}
    data["editable_fields"] = EDITABLE_FIELDS
    data["restricted_fields"] = RESTRICTED_FIELDS
    data["field_labels"] = field_labels
    return _render_profile(request, "ui/pages/student/profile_details.html", data)


# ---------------------------------------------------------------------------
# Profile edit
# ---------------------------------------------------------------------------


@student_required
@require_http_methods(["GET", "POST"])
def edit_profile(request: HttpRequest) -> HttpResponse:
    """Edit allowed student profile fields."""
    profile = _get_profile_or_404(request.user)
    if request.method == "POST":
        form = StudentProfileUpdateForm(request.POST, instance=profile)
        if form.is_valid():
            # StudentProfile.phone is the single owner; no User.phone mirror.
            form.save()
            messages.success(request, _("Profile updated."))
            return redirect("students:details")
    else:
        form = StudentProfileUpdateForm(instance=profile)
    return _render_profile(request, "ui/pages/student/profile.html", {"form": form})


# ---------------------------------------------------------------------------
# Application history
# ---------------------------------------------------------------------------


@student_required
def application_history(request: HttpRequest) -> HttpResponse:
    """Show all applications submitted by the student."""
    profile = _get_profile_or_404(request.user)
    applications = profile.program_applications.select_related("reviewed_by", "cohort__diploma", "course").prefetch_related("document_requests__upload", "intake_requests", "entrance_attempts").order_by("-created_at")
    page_obj, pagination_qs = paginate(request, applications, per_page=15)
    data = get_dashboard_data(profile, sections=["apply"])
    data.update({
        "applications": page_obj,
        "pagination_qs": pagination_qs,
        "application_rows": [get_application_row(application) for application in page_obj],
    })
    return _render_profile(request, "ui/pages/student/applications.html", data)


@student_required
def diploma_course(request: HttpRequest) -> HttpResponse:
    """Canonical Learning destination backed by the student selector cards."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile, sections=[
        "diploma_enrollments", "course_enrollments", "active_courses", "expired_courses",
        "assessment_attempts", "open_assessments",
    ])
    return _render_profile(request, "ui/pages/student/learning.html", data)


# ---------------------------------------------------------------------------
# Entrance exam results
# ---------------------------------------------------------------------------


@student_required
def exam_results(request: HttpRequest) -> HttpResponse:
    """Show entrance exam assignments and results."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile, sections=["exam_assignments"])
    return _render_profile(request, "ui/pages/student/exam_results.html", data)


# ---------------------------------------------------------------------------
# Assessment history
# ---------------------------------------------------------------------------


@student_required
def assessment_history(request: HttpRequest) -> HttpResponse:
    """Show normal assessment attempts, grades, feedback."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile, sections=["assessment_attempts", "offline_exam_records"])
    return _render_profile(request, "ui/pages/student/assessment_history.html", data)


# ---------------------------------------------------------------------------
# Progression history
# ---------------------------------------------------------------------------


@student_required
def progression_history(request: HttpRequest) -> HttpResponse:
    """Show academic lifecycle -- semesters, evaluations, promotions, completions."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile, sections=[
        "semester_progress", "promotion_decisions", "completions", "eligibilities", "grade_items",
        "record_history",
    ])
    return _render_profile(request, "ui/pages/student/progression.html", data)


@student_required
def record(request: HttpRequest) -> HttpResponse:
    """Canonical student record: released academic history and communications."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile, sections=[
        "record_history", "semester_progress", "promotion_decisions", "completions", "eligibilities",
        "grade_items", "certificates", "payments", "notifications", "assessment_attempts",
        "exam_assignments", "attendance",
    ])
    return _render_profile(request, "ui/pages/student/record.html", data)


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


@student_required
@require_http_methods(["GET", "POST"])
def apply(request: HttpRequest) -> HttpResponse:
    """Apply flow -- create a new ProgramApplication if eligible."""
    profile = _get_profile_or_404(request.user)
    selected_program_type = str(
        request.POST.get("program_type") if request.method == "POST" else request.GET.get("program_type", "diploma")
    ).strip()
    selected_target_id = str(
        request.POST.get("target_id") if request.method == "POST" else request.GET.get("target_id", "")
    ).strip()
    selected_delivery_mode = str(
        request.POST.get("delivery_mode") if request.method == "POST" else request.GET.get("delivery_mode", DELIVERY_MODE_ONLINE)
    ).strip()
    if selected_delivery_mode not in dict(DELIVERY_MODE_CHOICES):
        selected_delivery_mode = DELIVERY_MODE_ONLINE
    if selected_program_type not in {"diploma", "course"}:
        selected_program_type = "diploma"

    eligibility = check_apply_eligibility(profile)
    open_targets = get_open_apply_targets(
        program_type=selected_program_type,
        profile=profile,
        page_size=100,
    )
    cohorts = open_targets["items"] if selected_program_type == "diploma" else []
    courses = open_targets["items"] if selected_program_type == "course" else []
    context = {
        "eligibility": eligibility,
        "cohorts": cohorts,
        "courses": courses,
        "selected_program_type": selected_program_type,
        "selected_target_id": selected_target_id,
        "selected_delivery_mode": selected_delivery_mode,
        "delivery_mode_choices": DELIVERY_MODE_CHOICES,
    }

    if request.method == "POST":
        program_type = request.POST.get("program_type", "").strip()
        target_id = request.POST.get("target_id", "").strip()
        delivery_mode = request.POST.get("delivery_mode", "").strip()
        context.update({
            "selected_program_type": program_type,
            "selected_target_id": target_id,
            "selected_delivery_mode": delivery_mode,
        })
        if not program_type or not target_id:
            error_message = _("Please select a program.")
            messages.error(request, error_message)
            response = _render_profile(request, "ui/pages/student/apply.html", context)
            response = hx_toast(response, str(error_message), type='error')
            return response

        if program_type == "diploma":
            cohort = Cohort.objects.filter(pk=target_id).select_related("diploma").first()
            if not cohort:
                error_message = _("Invalid cohort selected.")
                messages.error(request, error_message)
                response = _render_profile(request, "ui/pages/student/apply.html", context)
                response = hx_toast(response, str(error_message), type='error')
                return response
            if not is_apply_target_open(cohort=cohort):
                error_message = _("This program is no longer accepting applications.")
                messages.error(request, error_message)
                response = _render_profile(request, "ui/pages/student/apply.html", context)
                response = hx_toast(response, str(error_message), type='error')
                return response
            if delivery_mode not in dict(DELIVERY_MODE_CHOICES):
                error_message = _("Please select an attendance mode.")
                messages.error(request, error_message)
                response = _render_profile(request, "ui/pages/student/apply.html", context)
                response = hx_toast(response, str(error_message), type='error')
                return response
            target_eligibility = check_apply_eligibility(profile, cohort=cohort)
            if not target_eligibility["allowed"]:
                reason = target_eligibility["reason"] or _("You are not eligible to apply.")
                messages.error(request, reason)
                response = _render_profile(request, "ui/pages/student/apply.html", context)
                response = hx_toast(response, str(reason), type='error')
                return response
            try:
                submit_apply_application(profile, cohort=cohort, delivery_mode=delivery_mode)
            except ValueError as exc:
                error_message = str(exc)
                messages.error(request, error_message)
                response = _render_profile(request, "ui/pages/student/apply.html", context)
                response = hx_toast(response, error_message, type='error')
                return response
        elif program_type == "course":
            course_id = target_id.removeprefix("course_")
            course = Course.objects.filter(pk=course_id).first()
            if not course:
                error_message = _("Invalid course selected.")
                messages.error(request, error_message)
                response = _render_profile(request, "ui/pages/student/apply.html", context)
                response = hx_toast(response, str(error_message), type='error')
                return response
            if not is_apply_target_open(course=course):
                error_message = _("This course is not currently accepting applications.")
                messages.error(request, error_message)
                response = _render_profile(request, "ui/pages/student/apply.html", context)
                response = hx_toast(response, str(error_message), type='error')
                return response
            target_eligibility = check_apply_eligibility(profile, course=course)
            if not target_eligibility["allowed"]:
                reason = target_eligibility["reason"] or _("You are not eligible to apply.")
                messages.error(request, reason)
                response = _render_profile(request, "ui/pages/student/apply.html", context)
                response = hx_toast(response, str(reason), type='error')
                return response
            try:
                submit_apply_application(profile, course=course)
            except ValueError as exc:
                error_message = str(exc)
                messages.error(request, error_message)
                response = _render_profile(request, "ui/pages/student/apply.html", context)
                response = hx_toast(response, error_message, type='error')
                return response
        else:
            error_message = _("Invalid program type.")
            messages.error(request, error_message)
            response = _render_profile(request, "ui/pages/student/apply.html", context)
            response = hx_toast(response, str(error_message), type='error')
            return response

        messages.success(request, _("Your application has been submitted."))
        return redirect("students:applications")

    return _render_profile(request, "ui/pages/student/apply.html", context)
