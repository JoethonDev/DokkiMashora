from __future__ import annotations

import hashlib
import secrets
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.applications.constants import (
    EMAIL_ACTION_APPLICATION_SURVEY_REQUEST,
    PROGRAM_APP_STATUS_SURVEY_REQUESTED,
    PROGRAM_APP_STATUS_SURVEY_SUBMITTED,
)
from apps.applications.models import ProgramApplication
from apps.applications import services as application_services
from apps.communications.services import queue_program_application_email

from .constants import (
    INTAKE_REQUEST_STATUS_CANCELLED,
    INTAKE_REQUEST_STATUS_EXPIRED,
    INTAKE_REQUEST_STATUS_REQUESTED,
    INTAKE_REQUEST_STATUS_SUBMITTED,
    INTAKE_REQUESTABLE_STATUSES,
)
from .models import IntakeForm, IntakeFormAnswer, IntakeFormRequest, IntakeFormResponse
from .selectors import get_required_intake_form_for_application

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from apps.applications.models import ProgramApplication


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


@transaction.atomic
def request_intake_form_for_application(
    application,
    actor,
    *,
    expires_at=None,
    form=None,
    send_email=True,
) -> IntakeFormRequest:
    """Create an intake form request, send email, and set status to survey_requested."""
    if application.status not in INTAKE_REQUESTABLE_STATUSES:
        raise ValueError(_("Intake forms can only be requested for applications that are not yet rejected or activated."))
    if form is None:
        form = get_required_intake_form_for_application(application)
    if form is None:
        raise ValueError(_("No required intake form exists for this application."))

    if IntakeFormResponse.objects.filter(form=form, program_application=application).exists():
        raise ValueError(_("Intake form already submitted for this application."))

    if expires_at is None:
        expires_at = timezone.now() + timezone.timedelta(days=7)

    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw_token)

    # Cancel any existing open requests
    IntakeFormRequest.objects.filter(
        form=form, program_application=application,
        status=INTAKE_REQUEST_STATUS_REQUESTED,
    ).update(
        status=INTAKE_REQUEST_STATUS_CANCELLED,
        cancelled_at=timezone.now(),
        cancelled_by=actor,
    )

    intake_request = IntakeFormRequest.objects.create(
        form=form,
        program_application=application,
        status=INTAKE_REQUEST_STATUS_REQUESTED,
        token_hash=token_hash,
        expires_at=expires_at,
        requested_by=actor,
        requested_at=timezone.now(),
    )

    old_status = application.status
    if application.status not in (PROGRAM_APP_STATUS_SURVEY_REQUESTED, PROGRAM_APP_STATUS_SURVEY_SUBMITTED):
        _set_status(application, PROGRAM_APP_STATUS_SURVEY_REQUESTED)
        application_services._record_decision(
            application, "request_survey",
            old_status, PROGRAM_APP_STATUS_SURVEY_REQUESTED, actor,
        )

    if send_email:
        transaction.on_commit(lambda: _send_intake_request_email(application, form, raw_token, intake_request))

    return intake_request


def _set_status(application, new_status):
    application.status = new_status
    application.save(update_fields=["status", "updated_at"])


def _send_intake_request_email(application, form, raw_token, intake_request) -> None:
    email = application.student.user.email
    if not email:
        return
    site_url = getattr(settings, "SITE_URL", "http://localhost:8000")
    form_url = f"{site_url}/intake/forms/fill/{raw_token}/"
    context = {
        "full_name": application.student.full_name or "",
        "program_title": _get_program_title(application),
        "form_title": form.title,
        "form_url": form_url,
        "expires_at": intake_request.expires_at.isoformat(),
        "staff_phone": "",
    }
    queue_program_application_email(
        program_application=application,
        action=EMAIL_ACTION_APPLICATION_SURVEY_REQUEST,
        template_slug="application_survey_request",
        to_email=email,
        context=context,
        sent_by=intake_request.requested_by,
        idempotency_key=f"application:survey_request:app:{application.pk}:request:{intake_request.pk}",
        reference=f"app:{application.pk}",
    )


def _get_program_title(application) -> str:
    if application.cohort and application.cohort.diploma:
        return application.cohort.diploma.title
    if application.course:
        return application.course.title
    return ""


def get_intake_request_by_token(raw_token: str) -> IntakeFormRequest | None:
    """Look up an intake request by its raw token (hashed lookup)."""
    token_hash = _hash_token(raw_token)
    try:
        return IntakeFormRequest.objects.select_related(
            "form", "program_application__student__user",
        ).get(token_hash=token_hash)
    except IntakeFormRequest.DoesNotExist:
        return None


def expire_intake_request(intake_request) -> bool:
    """Transition a single intake request to expired with a status guard.

    Only a request still in ``requested`` may be expired; a cancelled or
    submitted request is terminal and must never regress to expired. The row
    is locked so the transition cannot race another state change. Returns True
    when the transition was applied and False when the request was already
    expired (idempotent).
    """
    with transaction.atomic():
        request_row = IntakeFormRequest.objects.select_for_update().get(pk=intake_request.pk)
        if request_row.status == INTAKE_REQUEST_STATUS_EXPIRED:
            return False
        if request_row.status != INTAKE_REQUEST_STATUS_REQUESTED:
            raise ValueError(_("Only a requested intake form can be expired."))
        request_row.status = INTAKE_REQUEST_STATUS_EXPIRED
        request_row.save(update_fields=["status"])
    return True


def expire_stale_intake_requests() -> dict:
    """Idempotent lifecycle normalization for expired intake requests.

    Set-based conditional update: only ``requested`` requests whose expiry has
    passed become expired; cancelled and submitted requests are never touched.
    Read paths independently enforce expiry at render/submit time, so a delayed
    run never grants access. Scheduler-facing, bounded, returns counts only.
    """
    now = timezone.now()
    count = IntakeFormRequest.objects.filter(
        status=INTAKE_REQUEST_STATUS_REQUESTED,
        expires_at__lte=now,
    ).update(status=INTAKE_REQUEST_STATUS_EXPIRED)
    return {"intake_expired": count}


@transaction.atomic
def submit_intake_form_response(
    request_or_form: IntakeFormRequest | IntakeForm,
    actor: User,
    *,
    application: ProgramApplication,
    answers: dict,
) -> IntakeFormResponse:
    """Submit intake form answers and attempt activation.

    The request's application, the supplied application, and the actor must
    form one consistent tuple (A7): the intake request is locked and must
    belong to the supplied application before any response row is created.
    """
    if isinstance(request_or_form, IntakeFormRequest):
        intake_request = IntakeFormRequest.objects.select_for_update().select_related(
            "form", "program_application",
        ).get(pk=request_or_form.pk)
        if intake_request.program_application_id != application.pk:
            raise ValueError(_("This intake form link does not belong to this application."))
        form = intake_request.form
        if intake_request.status == INTAKE_REQUEST_STATUS_SUBMITTED:
            raise ValueError(_("This intake form request has already been submitted."))
        if intake_request.status == INTAKE_REQUEST_STATUS_CANCELLED:
            raise ValueError(_("This intake form request has been cancelled."))
        if intake_request.status == INTAKE_REQUEST_STATUS_EXPIRED:
            raise ValueError(_("This intake form request has expired."))
        if intake_request.expires_at <= timezone.now():
            expire_intake_request(intake_request)
            raise ValueError(_("This intake form request has expired."))
    elif isinstance(request_or_form, IntakeForm):
        intake_request = None
        form = request_or_form
    else:
        raise ValueError(_("Invalid request or form."))

    # ponytail: service-level ownership guard — actor must match the application's student user
    if application.student.user_id != actor.pk:
        raise PermissionError(_("You do not own this application."))

    # Lock the application row so the response creation and status transition
    # cannot race another submission for the same application.
    application = ProgramApplication.objects.select_for_update().get(pk=application.pk)

    if IntakeFormResponse.objects.filter(form=form, program_application=application).exists():
        raise ValueError(_("A response for this form already exists."))

    active_fields = {str(f.pk): f for f in form.fields.filter(is_active=True)}
    given_field_ids = set(answers.keys())
    valid_field_ids = set(active_fields.keys())
    unknown = given_field_ids - valid_field_ids
    if unknown:
        raise ValueError(_("Unknown field IDs: {}").format(", ".join(str(x) for x in unknown)))

    response = IntakeFormResponse.objects.create(
        form=form,
        student=application.student,
        program_application=application,
        request=intake_request,
        submitted_at=timezone.now(),
    )

    answer_objs = []
    for field_id, value in answers.items():
        field = active_fields[str(field_id)]
        answer_objs.append(
            IntakeFormAnswer(
                response=response, field=field, value=value,
            )
        )
    IntakeFormAnswer.objects.bulk_create(answer_objs)

    if intake_request:
        intake_request.status = INTAKE_REQUEST_STATUS_SUBMITTED
        intake_request.submitted_at = timezone.now()
        intake_request.save(update_fields=["status", "submitted_at"])

    old_status = application.status
    application.status = PROGRAM_APP_STATUS_SURVEY_SUBMITTED
    application.save(update_fields=["status", "updated_at"])
    application_services._record_decision(
        application, "submit_survey",
        old_status, PROGRAM_APP_STATUS_SURVEY_SUBMITTED, actor,
    )

    result = application_services.activate_application_if_ready(application, actor=actor)
    if not result.activated:
        application_services.send_post_activation_application_email(application, actor=actor)

    return response


def ensure_required_intake_request(application, actor=None) -> IntakeFormRequest | None:
    """Create an intake request if a required form exists and no response yet."""
    form = get_required_intake_form_for_application(application)
    if form is None:
        return None
    if IntakeFormResponse.objects.filter(form=form, program_application=application).exists():
        return None
    existing_open = IntakeFormRequest.objects.filter(
        form=form, program_application=application,
        status=INTAKE_REQUEST_STATUS_REQUESTED,
    ).first()
    if existing_open:
        return existing_open
    return request_intake_form_for_application(
        application, actor=actor, form=form, send_email=True,
    )
