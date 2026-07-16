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
from apps.applications.services import (
    _log_email_send,
    _record_decision,
    activate_application_if_ready,
)
from apps.communications.services import send_templated_email

from .constants import (
    INTAKE_REQUEST_STATUS_CANCELLED,
    INTAKE_REQUEST_STATUS_EXPIRED,
    INTAKE_REQUEST_STATUS_REQUESTED,
    INTAKE_REQUEST_STATUS_SUBMITTED,
)
from .models import IntakeForm, IntakeFormAnswer, IntakeFormRequest, IntakeFormResponse
from .selectors import get_required_intake_form_for_application

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from apps.applications.models import ProgramApplication


def can_request_intake_form(application) -> bool:
    """Check if an intake request can be sent for this application."""
    form = get_required_intake_form_for_application(application)
    if form is None:
        return False
    if IntakeFormResponse.objects.filter(form=form, program_application=application).exists():
        return False
    return True


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
        _record_decision(
            application, "request_survey",
            old_status, PROGRAM_APP_STATUS_SURVEY_REQUESTED, actor,
        )

    if send_email:
        transaction.on_commit(lambda: _send_intake_request_email(application, form, raw_token, intake_request))
        email_action = EMAIL_ACTION_APPLICATION_SURVEY_REQUEST
        _log_email_send(application, email_action, "application_survey_request", application.student.user.email, actor)

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
    send_templated_email("application_survey_request", email, context, sent_by=intake_request.requested_by)


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


@transaction.atomic
def submit_intake_form_response(
    request_or_form: IntakeFormRequest | IntakeForm,
    actor: User,
    *,
    application: ProgramApplication,
    answers: dict,
) -> IntakeFormResponse:
    """Submit intake form answers and attempt activation."""
    if isinstance(request_or_form, IntakeFormRequest):
        intake_request = request_or_form
        form = request_or_form.form
        if intake_request.status == INTAKE_REQUEST_STATUS_SUBMITTED:
            raise ValueError(_("This intake form request has already been submitted."))
        if intake_request.status == INTAKE_REQUEST_STATUS_EXPIRED:
            raise ValueError(_("This intake form request has expired."))
        if intake_request.expires_at <= timezone.now():
            intake_request.status = INTAKE_REQUEST_STATUS_EXPIRED
            intake_request.save(update_fields=["status"])
            raise ValueError(_("This intake form request has expired."))
    elif isinstance(request_or_form, IntakeForm):
        intake_request = None
        form = request_or_form
    else:
        raise ValueError(_("Invalid request or form."))

    # ponytail: service-level ownership guard — actor must match the application's student user
    if application.student.user_id != actor.pk:
        raise PermissionError(_("You do not own this application."))

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
    _record_decision(
        application, "submit_survey",
        old_status, PROGRAM_APP_STATUS_SURVEY_SUBMITTED, actor,
    )

    activate_application_if_ready(application, actor=actor)

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
