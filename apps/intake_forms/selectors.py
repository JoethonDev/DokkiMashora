from __future__ import annotations

from django.db.models import Prefetch
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.academics.models import Cohort, CourseVersion
from apps.applications.constants import (
    PROGRAM_APP_STATUS_DOCUMENTS_SUBMITTED,
    PROGRAM_TYPE_COURSE,
    PROGRAM_TYPE_DIPLOMA,
)
from apps.applications.models import ProgramApplication

from .constants import (
    INTAKE_REQUEST_STATUS_EXPIRED,
    INTAKE_REQUEST_STATUS_REQUESTED,
    INTAKE_REQUESTABLE_STATUSES,
)
from .models import IntakeForm, IntakeFormRequest, IntakeFormResponse

ACTIVE_COURSE_VERSION_ORDERING = ("-created_at", "-id")
REQUIRED_INTAKE_FORM_ORDERING = ("pk",)


def get_application_intake_target(application: ProgramApplication) -> CourseVersion | Cohort | None:
    """Resolve the intake target (CourseVersion or Cohort) for an application.

    Uses the same resolution as activation:
    - Course: latest active CourseVersion for the app's course
    - Diploma: app's cohort directly
    """
    if application.program_type == PROGRAM_TYPE_COURSE:
        if not application.course:
            return None
        prefetched_versions = getattr(application.course, "_active_intake_versions", None)
        if prefetched_versions is not None:
            return prefetched_versions[0] if prefetched_versions else None
        return application.course.versions.filter(is_active=True).order_by(*ACTIVE_COURSE_VERSION_ORDERING).first()
    if application.program_type == PROGRAM_TYPE_DIPLOMA:
        return application.cohort
    return None


def get_required_intake_forms_for_application(application: ProgramApplication) -> list[IntakeForm]:
    """Return every active required form for the exact application target.

    The form window controls whether a request may be issued, not whether the
    requirement exists.  Keeping those concepts separate prevents a closed or
    expired required form from becoming an accidental payment/activation pass.
    """
    target = get_application_intake_target(application)
    if target is None:
        return []
    prefetched_forms = getattr(target, "_prefetched_objects_cache", {}).get("intake_forms")
    if prefetched_forms is not None:
        return sorted(
            (
                form for form in prefetched_forms
                if form.is_active and form.is_required
            ),
            key=lambda form: form.pk,
        )

    qs = IntakeForm.objects.filter(is_active=True, is_required=True)
    if isinstance(target, CourseVersion):
        qs = qs.filter(course_version=target)
    elif isinstance(target, Cohort):
        qs = qs.filter(cohort=target)
    else:
        return []
    return list(qs.order_by(*REQUIRED_INTAKE_FORM_ORDERING))


def get_required_intake_form_for_application(application: ProgramApplication) -> IntakeForm | None:
    """Return the first currently open required form for request creation.

    This compatibility helper intentionally does not represent completion;
    use :func:`has_required_intake_response` for the lifecycle gate.
    """
    for form in get_required_intake_forms_for_application(application):
        if form.is_open():
            return form
    return None


def get_open_required_intake_forms_for_application(application: ProgramApplication) -> list[IntakeForm]:
    """Return the open subset of active required forms."""
    return [form for form in get_required_intake_forms_for_application(application) if form.is_open()]


def get_intake_requirement_states(application: ProgramApplication, *, now=None) -> list[dict]:
    """Project every required form and its request/response state."""
    now = now or timezone.now()
    forms = get_required_intake_forms_for_application(application)
    form_ids = {form.pk for form in forms}
    prefetched_objects = getattr(application, "_prefetched_objects_cache", {})
    prefetched_responses = prefetched_objects.get("intake_responses")
    if prefetched_responses is not None:
        response_form_ids = {
            response.form_id for response in prefetched_responses
            if response.form_id in form_ids
        }
    else:
        response_form_ids = set(
            IntakeFormResponse.objects.filter(
                program_application=application,
                form_id__in=form_ids,
            ).values_list("form_id", flat=True)
        )
    prefetched_requests = prefetched_objects.get("intake_requests")
    if prefetched_requests is not None:
        requests = sorted(
            (
                request for request in prefetched_requests
                if request.form_id in form_ids
            ),
            key=lambda request: (request.requested_at, request.pk),
            reverse=True,
        )
    else:
        requests = list(
            IntakeFormRequest.objects.filter(
                form_id__in=form_ids,
                program_application=application,
            ).order_by("-requested_at", "-id")
        )
    requests_by_form: dict[int, list[IntakeFormRequest]] = {}
    for request in requests:
        requests_by_form.setdefault(request.form_id, []).append(request)

    states = []
    for form in forms:
        form_requests = requests_by_form.get(form.pk, [])
        latest_request = form_requests[0] if form_requests else None
        form_open = (
            form.is_active
            and (form.open_at is None or form.open_at <= now)
            and (form.close_at is None or form.close_at > now)
        )
        open_request = next(
            (
                request for request in form_requests
                if request.status == INTAKE_REQUEST_STATUS_REQUESTED
                and request.expires_at > now
                and form_open
            ),
            None,
        )
        if form.pk in response_form_ids:
            state, label = "submitted", _("Survey submitted")
        elif open_request:
            state, label = "requested", _("Survey requested")
        elif not form_open:
            if latest_request and latest_request.status == INTAKE_REQUEST_STATUS_EXPIRED:
                state, label = "expired", _("Required survey expired")
            else:
                state, label = "closed", _("Required survey is closed")
        elif latest_request and latest_request.status == INTAKE_REQUEST_STATUS_EXPIRED:
            state, label = "expired", _("Required survey expired")
        else:
            state, label = "available", _("Required survey is ready")
        states.append({
            "form": form,
            "state": state,
            "state_label": label,
            "has_response": form.pk in response_form_ids,
            "form_open": form_open,
            "latest_request": latest_request,
            "open_request": open_request,
        })
    return states


def has_required_intake_response(application: ProgramApplication) -> bool:
    """Return true only when every active required form has a response.

    A missing currently-open form is satisfied because no active requirement
    exists.  A closed/expired active required form remains unsatisfied until it
    has a response or an explicitly governed requirement change removes it.
    """
    forms = get_required_intake_forms_for_application(application)
    if not forms:
        return True
    response_count = IntakeFormResponse.objects.filter(
        form_id__in=[form.pk for form in forms],
        program_application=application,
    ).values("form_id").distinct().count()
    return response_count == len(forms)


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
    states = get_intake_requirement_states(application)
    if not states:
        return {
            "required_form": None,
            "required_forms": [],
            "has_response": True,
            "form_open": False,
            "can_request": False,
            "blocked_reason": None,
        }
    pending = [item for item in states if not item["has_response"]]
    open_pending = [item for item in pending if item["form_open"]]
    latest_request = next(
        (item["latest_request"] for item in pending if item["latest_request"] is not None),
        None,
    )
    can_request = (
        application.status in INTAKE_REQUESTABLE_STATUSES
        and bool(open_pending)
        and (
            application.program_type != PROGRAM_TYPE_DIPLOMA
            or application.status == PROGRAM_APP_STATUS_DOCUMENTS_SUBMITTED
        )
    )
    blocked_reason = None
    if pending and not open_pending:
        blocked_reason = _(
            "A required survey is closed or expired. Admissions must open or replace it before payment or activation."
        )
    return {
        "required_form": states[0]["form"],
        "required_forms": states,
        "has_response": not pending,
        "form_open": bool(open_pending),
        "latest_request": latest_request,
        "can_request": can_request,
        "blocked_reason": blocked_reason,
    }
