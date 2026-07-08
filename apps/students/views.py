"""Student profile views -- dashboard, details, history sections, apply."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template import engines
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE, SCOPE_COURSE_VERSION
from apps.academics.models import (
    Cohort,
    Course,
    CourseEnrollment,
    CourseVersion,
    DiplomaEnrollment,
    LectureWatchProgress,
    ModeratorStudentAssignment,
    TeacherAssignment,
)
from apps.assessments.constants import (
    GRADE_VISIBILITY_HIDDEN,
    GRADE_VISIBILITY_RELEASED,
    GRADE_VISIBILITY_UNDER_REVIEW,
)
from apps.core.htmx import is_htmx
from apps.core.pagination import paginate
from apps.entrance_exams.constants import ATTEMPT_STATUS_GRADED

from .forms import StudentProfileUpdateForm
from .policies import EDITABLE_FIELDS, RESTRICTED_FIELDS, get_field_label
from .selectors import get_dashboard_data
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
    """Render just the profile_content block for HTMX requests."""
    engine = engines["django"]
    source = (
        "{% extends '" + template_name.replace("'", "\\'") + "' %}"
        "{% block skip_link %}{% endblock %}"
        "{% block navbar %}{% endblock %}"
        "{% block mobile_nav %}{% endblock %}"
        "{% block messages %}{% endblock %}"
        "{% block footer %}{% endblock %}"
        "{% block extra_js %}{% endblock %}"
        "{% block extra_head %}{% endblock %}"
        "{% block content %}"
        '<div id="profile-content">'
        "{% include 'partials/_messages.html' %}"
        "{% block profile_content %}{{ block.super }}{% endblock %}"
        "</div>"
        "{% endblock %}"
    )
    return HttpResponse(engine.from_string(source).render(context, request))


def _render_profile(request, template_name, context):
    """Full page or HTMX fragment depending on request."""
    if is_htmx(request):
        return _render_htmx(request, template_name, context)
    return render(request, template_name, context)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    """Student profile dashboard -- aggregation overview."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile)
    return _render_profile(request, "students/profile/dashboard.html", data)


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
    return _render_profile(request, "students/profile/details.html", data)


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
    return _render_profile(request, "students/profile/edit.html", {"form": form})


# ---------------------------------------------------------------------------
# Application history
# ---------------------------------------------------------------------------


@login_required
def application_history(request: HttpRequest) -> HttpResponse:
    """Show all applications submitted by the student."""
    profile = _get_profile_or_404(request.user)
    applications = profile.program_applications.select_related("reviewed_by", "cohort__diploma", "course").prefetch_related("document_requests__upload").order_by("-created_at")
    page_obj, pagination_qs = paginate(request, applications, per_page=15)
    data = get_dashboard_data(profile, sections=["apply"])
    data.update({"applications": page_obj, "pagination_qs": pagination_qs})
    return _render_profile(request, "students/profile/applications.html", data)


# ---------------------------------------------------------------------------
# Enrollment history
# ---------------------------------------------------------------------------


@login_required
def enrollment_history(request: HttpRequest) -> HttpResponse:
    """Show all diploma and course enrollments."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile, sections=["diploma_enrollments", "course_enrollments"])
    return _render_profile(request, "students/profile/enrollments.html", data)


@login_required
def diploma_course(request: HttpRequest) -> HttpResponse:
    """Show the student's diploma and course enrollments listing."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile, sections=["diploma_enrollments", "course_enrollments"])
    items = []
    for de in (data.get("diploma_enrollments") or []):
        cohort = de.cohort
        for cv in cohort.course_versions.select_related("course").prefetch_related("lectures"):
            lecture_count = cv.lectures.filter(is_published=True).count()
            items.append({
                "type": "diploma",
                "label": cohort.diploma.title,
                "course_title": cv.course.title,
                "cv_id": cv.id,
                "joined_at": de.activated_at or de.created_at,
                "expires_at": de.expires_at,
                "lecture_count": lecture_count,
            })

    for ce in (data.get("course_enrollments") or []):
        cv = ce.course_version
        lecture_count = cv.lectures.filter(is_published=True).count()
        items.append({
            "type": "course",
            "label": "",
            "course_title": cv.course.title,
            "cv_id": cv.id,
            "joined_at": ce.activated_at or ce.created_at,
            "expires_at": ce.expires_at,
            "lecture_count": lecture_count,
        })

    return _render_profile(request, "students/profile/diploma_course.html", {"items": items})


@login_required
def diploma_course_detail(request: HttpRequest, version_id: int) -> HttpResponse:
    """Show course content, teacher, and moderator within the profile."""
    profile = _get_profile_or_404(request.user)
    cv = get_object_or_404(CourseVersion.objects.select_related("course", "cohort"), id=version_id, is_active=True)
    has_access = CourseEnrollment.objects.filter(student=profile, course_version=cv, status=ENROLLMENT_STATUS_ACTIVE).exists()
    if not has_access:
        has_access = DiplomaEnrollment.objects.filter(
            student=profile, cohort=cv.cohort, status=ENROLLMENT_STATUS_ACTIVE
        ).exists()
    if not has_access:
        raise PermissionDenied
    lectures = list(
        cv.lectures.filter(is_published=True).order_by("order").only("id", "title", "order", "youtube_url", "audio_url", "audio_hls_playlist_key")
    )
    completed_ids = set(
        LectureWatchProgress.objects.filter(
            student=profile, lecture__in=lectures, status="completed"
        ).values_list("lecture_id", flat=True)
    )
    teachers = list(
        TeacherAssignment.objects.filter(scope_type=SCOPE_COURSE_VERSION, course_version=cv, active=True)
        .select_related("teacher")
    )
    moderator = None
    if cv.cohort_id:
        m = ModeratorStudentAssignment.objects.filter(
            student=profile, cohort_id=cv.cohort_id, active=True
        ).select_related("moderator").first()
        moderator = m.moderator if m else None
    return _render_profile(request, "students/profile/diploma_course_detail.html", {
        "course_version": cv, "lectures": lectures, "teachers": teachers, "moderator": moderator,
        "completed_lecture_ids": completed_ids,
    })


# ---------------------------------------------------------------------------
# Access list (active + expired/restricted)
# ---------------------------------------------------------------------------


@login_required
def access_list(request: HttpRequest) -> HttpResponse:
    """Show active and expired/restricted access."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile, sections=["active_courses", "expired_courses"])
    return _render_profile(request, "students/profile/access.html", data)


# ---------------------------------------------------------------------------
# Entrance exam results
# ---------------------------------------------------------------------------


@login_required
def exam_results(request: HttpRequest) -> HttpResponse:
    """Show entrance exam assignments and results."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile, sections=["exam_assignments"])
    data["attempt_status_graded"] = ATTEMPT_STATUS_GRADED
    return _render_profile(request, "students/profile/exams.html", data)


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
    return _render_profile(request, "students/profile/assessments.html", data)


# ---------------------------------------------------------------------------
# Homework submissions
# ---------------------------------------------------------------------------


@login_required
def homework_list(request: HttpRequest) -> HttpResponse:
    """Show homework submissions."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile, sections=["homework_submissions"])
    return _render_profile(request, "students/profile/homework.html", data)


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
    return _render_profile(request, "students/profile/progression.html", data)


# ---------------------------------------------------------------------------
# Certificates
# ---------------------------------------------------------------------------


@login_required
def certificate_list(request: HttpRequest) -> HttpResponse:
    """Show available certificates."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile, sections=["certificates"])
    return _render_profile(request, "students/profile/certificates.html", data)


# ---------------------------------------------------------------------------
# Payment status (placeholder)
# ---------------------------------------------------------------------------


@login_required
def payment_status(request: HttpRequest) -> HttpResponse:
    """Show payment status -- placeholder until Phase 8."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile, sections=["payments"])
    return _render_profile(request, "students/profile/payments.html", data)


# ---------------------------------------------------------------------------
# Notification history
# ---------------------------------------------------------------------------


@login_required
def notification_history(request: HttpRequest) -> HttpResponse:
    """Show email/notification history."""
    profile = _get_profile_or_404(request.user)
    data = get_dashboard_data(profile, sections=["notifications"])
    return _render_profile(request, "students/profile/notifications.html", data)


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


@login_required
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
    if selected_program_type not in {"diploma", "course"}:
        selected_program_type = "diploma"

    eligibility = check_apply_eligibility(profile)
    open_targets = get_open_apply_targets(
        program_type=selected_program_type,
        profile=profile,
        page_size=1000 if selected_program_type == "diploma" else 10,
    )

    cohorts = open_targets["items"] if selected_program_type == "diploma" else []
    courses = open_targets["items"] if selected_program_type == "course" else []

    if request.method == "POST":
        program_type = request.POST.get("program_type")
        target_id = request.POST.get("target_id")
        if not program_type or not target_id:
            messages.error(request, _("Please select a program."))
            return _render_profile(request, "students/profile/apply.html", {
                "eligibility": eligibility,
                "cohorts": cohorts,
                "courses": courses,
                "selected_program_type": selected_program_type,
                "selected_target_id": selected_target_id,
            })

        if program_type == "diploma":
            cohort = Cohort.objects.filter(pk=target_id).select_related("diploma").first()
            if not cohort:
                messages.error(request, _("Invalid cohort selected."))
                return _render_profile(request, "students/profile/apply.html", {
                    "eligibility": eligibility,
                    "cohorts": cohorts,
                    "courses": courses,
                    "selected_program_type": program_type,
                    "selected_target_id": target_id,
                })
            cohort_eligibility = check_apply_eligibility(profile, cohort=cohort)
            if not cohort_eligibility["allowed"]:
                messages.error(request, cohort_eligibility["reason"] or _("You are not eligible to apply."))
                return _render_profile(request, "students/profile/apply.html", {
                    "eligibility": eligibility,
                    "cohorts": cohorts,
                    "courses": courses,
                    "selected_program_type": program_type,
                    "selected_target_id": target_id,
                })
            try:
                submit_apply_application(profile, cohort=cohort)
            except ValueError as exc:
                messages.error(request, str(exc))
                return _render_profile(request, "students/profile/apply.html", {
                    "eligibility": eligibility,
                    "cohorts": cohorts,
                    "courses": courses,
                    "selected_program_type": program_type,
                    "selected_target_id": target_id,
                })
        elif program_type == "course":
            target_value = str(target_id)
            course_id = target_value.split("_", 1)[1] if target_value.startswith("course_") else target_value
            course = Course.objects.filter(pk=course_id, is_active=True).first()
            if not course:
                messages.error(request, _("Invalid course selected."))
                return _render_profile(request, "students/profile/apply.html", {
                    "eligibility": eligibility,
                    "cohorts": cohorts,
                    "courses": courses,
                    "selected_program_type": program_type,
                    "selected_target_id": target_id,
                })
            course_eligibility = check_apply_eligibility(profile, course=course)
            if not course_eligibility["allowed"]:
                messages.error(request, course_eligibility["reason"] or _("You are not eligible to apply."))
                return _render_profile(request, "students/profile/apply.html", {
                    "eligibility": eligibility,
                    "cohorts": cohorts,
                    "courses": courses,
                    "selected_program_type": program_type,
                    "selected_target_id": target_id,
                })
            try:
                submit_apply_application(profile, course=course)
            except ValueError as exc:
                messages.error(request, str(exc))
                return _render_profile(request, "students/profile/apply.html", {
                    "eligibility": eligibility,
                    "cohorts": cohorts,
                    "courses": courses,
                    "selected_program_type": program_type,
                    "selected_target_id": target_id,
                })
        else:
            messages.error(request, _("Invalid program type."))
            return _render_profile(request, "students/profile/apply.html", {
                "eligibility": eligibility,
                "cohorts": cohorts,
                "courses": courses,
                "selected_program_type": program_type,
                "selected_target_id": target_id,
            })

        messages.success(request, _("Your application has been submitted."))
        return redirect("students:application_history")

    return _render_profile(request, "students/profile/apply.html", {
        "eligibility": eligibility,
        "cohorts": cohorts,
        "courses": courses,
        "selected_program_type": selected_program_type,
        "selected_target_id": selected_target_id,
    })
