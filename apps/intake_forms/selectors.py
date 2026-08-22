from __future__ import annotations

from django.db.models import Prefetch

from apps.academics.models import Cohort, CourseVersion
from apps.applications.constants import PROGRAM_TYPE_COURSE, PROGRAM_TYPE_DIPLOMA
from apps.applications.models import ProgramApplication

from .constants import INTAKE_REQUESTABLE_STATUSES
from .models import IntakeForm, IntakeFormRequest, IntakeFormResponse


def get_application_intake_target(application: ProgramApplication) -> CourseVersion | Cohort | None:
    """Resolve the intake target (CourseVersion or Cohort) for an application.

    Uses the same resolution as activation:
    - Course: latest active CourseVersion for the app's course
    - Diploma: app's cohort directly
    """
    if application.program_type == PROGRAM_TYPE_COURSE:
        if not application.course:
            return None
        return application.course.versions.filter(is_active=True).order_by("-created_at").first()
    if application.program_type == PROGRAM_TYPE_DIPLOMA:
        return application.cohort
    return None


def get_required_intake_form_for_application(application: ProgramApplication) -> IntakeForm | None:
    """Return the required active intake form for this application's target, or None."""
    target = get_application_intake_target(application)
    if target is None:
        return None
    qs = IntakeForm.objects.filter(is_active=True, is_required=True)
    if isinstance(target, CourseVersion):
        qs = qs.filter(course_version=target)
    elif isinstance(target, Cohort):
        qs = qs.filter(cohort=target)
    else:
        return None
    for form in qs:
        if form.is_open():
            return form
    return None


def has_required_intake_response(application: ProgramApplication) -> bool:
    """Check if the application has a submitted response for its required form."""
    form = get_required_intake_form_for_application(application)
    if form is None:
        return True
    return IntakeFormResponse.objects.filter(
        form=form,
        program_application=application,
    ).exists()


def get_intake_responses_for_target(target) -> list[dict]:
    """Return response data for a given target (CourseVersion or Cohort)."""
    if isinstance(target, CourseVersion):
        forms = IntakeForm.objects.filter(course_version=target)
    elif isinstance(target, Cohort):
        forms = IntakeForm.objects.filter(cohort=target)
    else:
        return []
    responses = IntakeFormResponse.objects.filter(
        form__in=forms,
    ).select_related(
        "student__user", "program_application",
    ).prefetch_related(
        Prefetch("answers", to_attr="prefetched_answers"),
        "answers__field",
    ).order_by("-submitted_at")
    result = []
    for r in responses:
        result.append({
            "response": r,
            "student_name": r.student.full_name,
            "student_email": r.student.user.email,
            "application_id": r.program_application_id,
            "submitted_at": r.submitted_at,
            "answers": r.prefetched_answers,
        })
    return result


def get_application_intake_state(application: ProgramApplication) -> dict:
    """Return intake form state for an application (used in admin detail)."""
    form = get_required_intake_form_for_application(application)
    if form is None:
        return {"required_form": None, "has_response": True, "form_open": False, "can_request": False}
    has_response = IntakeFormResponse.objects.filter(
        form=form, program_application=application,
    ).exists()
    latest_request = IntakeFormRequest.objects.filter(
        form=form, program_application=application,
    ).order_by("-requested_at").first()
    return {
        "required_form": form,
        "has_response": has_response,
        "form_open": form.is_open(),
        "latest_request": latest_request,
        "can_request": application.status in INTAKE_REQUESTABLE_STATUSES,
    }
