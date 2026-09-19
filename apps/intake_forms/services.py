from __future__ import annotations

import hashlib
import secrets
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.applications import services as application_services
from apps.applications.constants import (
    DECISION_ACTION_REQUEST_SURVEY,
    DECISION_ACTION_SUBMIT_SURVEY,
    EMAIL_ACTION_APPLICATION_SURVEY_REQUEST,
    PROGRAM_APP_STATUS_SURVEY_REQUESTED,
    PROGRAM_APP_STATUS_SURVEY_SUBMITTED,
    PROGRAM_TYPE_DIPLOMA,
)
from apps.applications.models import ProgramApplication
from apps.communications.services import queue_program_application_email

from .constants import (
    INTAKE_REQUEST_STATUS_CANCELLED,
    INTAKE_REQUEST_STATUS_EXPIRED,
    INTAKE_REQUEST_STATUS_REQUESTED,
    INTAKE_REQUEST_STATUS_SUBMITTED,
    INTAKE_REQUESTABLE_STATUSES,
)
from .models import IntakeForm, IntakeFormAnswer, IntakeFormRequest, IntakeFormResponse
from .selectors import (
    get_open_required_intake_forms_for_application,
    get_required_intake_forms_for_application,
    has_required_intake_response,
)

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
    replace_existing=True,
) -> IntakeFormRequest:
    """Create or replace one exact required intake request under an app lock."""
    with transaction.atomic():
        locked = ProgramApplication.objects.select_for_update().get(pk=application.pk)
        if locked.status not in INTAKE_REQUESTABLE_STATUSES:
            raise ValueError(_("Intake forms can only be requested after the admissions decision and required document submission."))
        if locked.program_type == PROGRAM_TYPE_DIPLOMA:
            from apps.applications.documents import has_application_documents_submitted

            if not has_application_documents_submitted(locked):
                raise ValueError(_("Submit the required documents before requesting the survey."))

        required_forms = get_required_intake_forms_for_application(locked)
        required_form = form
        if required_form is None:
            required_form = next(
                (
                    candidate for candidate in required_forms
                    if candidate.is_open()
                    and not IntakeFormResponse.objects.filter(
                        form=candidate, program_application=locked,
                    ).exists()
                ),
                None,
            )
        if required_form is None:
            if required_forms:
                raise ValueError(_(
                    "A required intake form is closed or expired. Admissions must open or replace it before requesting a response."
                ))
            raise ValueError(_("No required intake form exists for this application."))
        if required_form.pk not in {candidate.pk for candidate in required_forms}:
            raise ValueError(_("The selected intake form is not required for this application."))
        if not required_form.is_open():
            raise ValueError(_("The required intake form is closed or expired. Open it before requesting a response."))

        if IntakeFormResponse.objects.filter(form=required_form, program_application=locked).exists():
            raise ValueError(_("Intake form already submitted for this application."))

        open_request = IntakeFormRequest.objects.filter(
            form=required_form,
            program_application=locked,
            status=INTAKE_REQUEST_STATUS_REQUESTED,
            expires_at__gt=timezone.now(),
        ).first()
        if open_request and not replace_existing:
            return open_request

        if expires_at is None:
            expires_at = timezone.now() + timezone.timedelta(days=7)

        raw_token = secrets.token_urlsafe(32)
        token_hash = _hash_token(raw_token)

        IntakeFormRequest.objects.filter(
            form=required_form, program_application=locked,
            status=INTAKE_REQUEST_STATUS_REQUESTED,
        ).update(
            status=INTAKE_REQUEST_STATUS_CANCELLED,
            cancelled_at=timezone.now(),
            cancelled_by=actor,
        )

        try:
            with transaction.atomic():
                intake_request = IntakeFormRequest.objects.create(
                    form=required_form,
                    program_application=locked,
                    status=INTAKE_REQUEST_STATUS_REQUESTED,
                    token_hash=token_hash,
                    expires_at=expires_at,
                    requested_by=actor,
                    requested_at=timezone.now(),
                )
        except IntegrityError:
            existing = IntakeFormRequest.objects.filter(
                form=required_form,
                program_application=locked,
                status=INTAKE_REQUEST_STATUS_REQUESTED,
            ).first()
            if existing:
                raise ValueError(_("An intake request is already open for this requirement.")) from None
            raise

        old_status = locked.status
        if locked.status != PROGRAM_APP_STATUS_SURVEY_REQUESTED:
            _set_status(locked, PROGRAM_APP_STATUS_SURVEY_REQUESTED)
            application_services._record_decision(
                locked, "request_survey",
                old_status, PROGRAM_APP_STATUS_SURVEY_REQUESTED, actor,
            )

        if send_email:
            transaction.on_commit(lambda: _send_intake_request_email(locked, required_form, raw_token, intake_request))

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


def submit_intake_form_response(
    request_or_form: IntakeFormRequest | IntakeForm,
    actor: User,
    *,
    application: ProgramApplication,
    answers: dict,
) -> IntakeFormResponse:
    response = _submit_intake_form_response_atomic(
        request_or_form,
        actor,
        application=application,
        answers=answers,
    )
    if response is None:
        raise ValueError(_("This intake form request has expired."))
    return response


@transaction.atomic
def _submit_intake_form_response_atomic(
    request_or_form: IntakeFormRequest | IntakeForm,
    actor: User,
    *,
    application: ProgramApplication,
    answers: dict,
) -> IntakeFormResponse | None:
    """Submit one owned request and advance only after all requirements pass."""
    if not isinstance(request_or_form, IntakeFormRequest):
        raise ValueError(_("Invalid request or form."))

    # Lock the application first, matching the application-owned workflow
    # services, then lock the exact request under that application.
    application = ProgramApplication.objects.select_for_update().select_related(
        "student__user",
    ).get(pk=application.pk)
    intake_request = IntakeFormRequest.objects.select_for_update().select_related(
        "form", "program_application",
    ).get(pk=request_or_form.pk)
    if intake_request.program_application_id != application.pk:
        raise ValueError(_("This intake form link does not belong to this application."))
    if application.student.user_id != actor.pk:
        raise PermissionError(_("You do not own this application."))
    if intake_request.program_application.student_id != application.student_id:
        raise ValueError(_("This intake form request has invalid ownership."))
    if application.status not in INTAKE_REQUESTABLE_STATUSES:
        raise ValueError(_("This intake form is no longer available for the application."))
    if application.program_type == PROGRAM_TYPE_DIPLOMA:
        from apps.applications.documents import has_application_documents_submitted

        if not has_application_documents_submitted(application):
            raise ValueError(_("Application documents must be submitted before completing the intake form."))
    if intake_request.status == INTAKE_REQUEST_STATUS_SUBMITTED:
        raise ValueError(_("This intake form request has already been submitted."))
    if intake_request.status == INTAKE_REQUEST_STATUS_CANCELLED:
        raise ValueError(_("This intake form request has been cancelled."))
    if intake_request.status == INTAKE_REQUEST_STATUS_EXPIRED:
        raise ValueError(_("This intake form request has expired."))
    if intake_request.expires_at <= timezone.now():
        intake_request.status = INTAKE_REQUEST_STATUS_EXPIRED
        intake_request.save(update_fields=["status"])
        # Return normally so the transaction commits the durable expiry state;
        # the public wrapper raises the user-facing validation error after the
        # atomic block has exited.
        return None

    form = intake_request.form
    if not form.is_open():
        raise ValueError(_("This required intake form is closed or expired."))
    if form.pk not in {
        required_form.pk for required_form in get_required_intake_forms_for_application(application)
    }:
        raise ValueError(_("This intake form is not required for the application."))
    if IntakeFormResponse.objects.filter(form=form, program_application=application).exists():
        raise ValueError(_("A response for this form already exists."))

    active_fields = {str(f.pk): f for f in form.fields.filter(is_active=True)}
    unknown = set(answers.keys()) - set(active_fields.keys())
    if unknown:
        raise ValueError(_("Unknown field IDs: {}").format(", ".join(str(x) for x in unknown)))

    response = IntakeFormResponse.objects.create(
        form=form,
        student=application.student,
        program_application=application,
        request=intake_request,
        submitted_at=timezone.now(),
    )
    IntakeFormAnswer.objects.bulk_create([
        IntakeFormAnswer(response=response, field=active_fields[str(field_id)], value=value)
        for field_id, value in answers.items()
    ])

    intake_request.status = INTAKE_REQUEST_STATUS_SUBMITTED
    intake_request.submitted_at = timezone.now()
    intake_request.save(update_fields=["status", "submitted_at"])

    all_responses = has_required_intake_response(application)
    response_metadata = {
        "form_id": form.pk,
        "request_id": intake_request.pk,
        "all_required_forms_submitted": all_responses,
    }
    if all_responses and application.status != PROGRAM_APP_STATUS_SURVEY_SUBMITTED:
        old_status = application.status
        application.status = PROGRAM_APP_STATUS_SURVEY_SUBMITTED
        application.save(update_fields=["status", "updated_at"])
        application_services._record_decision(
            application, DECISION_ACTION_SUBMIT_SURVEY,
            old_status, PROGRAM_APP_STATUS_SURVEY_SUBMITTED, actor,
            metadata=response_metadata,
        )
    elif not all_responses and application.status != PROGRAM_APP_STATUS_SURVEY_REQUESTED:
        old_status = application.status
        application.status = PROGRAM_APP_STATUS_SURVEY_REQUESTED
        application.save(update_fields=["status", "updated_at"])
        application_services._record_decision(
            application, DECISION_ACTION_REQUEST_SURVEY,
            old_status, PROGRAM_APP_STATUS_SURVEY_REQUESTED, actor,
            metadata=response_metadata,
        )
    else:
        application_services._record_decision(
            application,
            DECISION_ACTION_SUBMIT_SURVEY,
            application.status,
            application.status,
            actor,
            metadata=response_metadata,
        )

    if all_responses:
        result = application_services.activate_application_if_ready(application, actor=actor)
        if not result.activated:
            application_services.send_post_activation_application_email(application, actor=actor)

    return response


def ensure_required_intake_request(application, actor=None) -> IntakeFormRequest | None:
    """Ensure one open request exists for the first unresolved open form."""
    for form in get_open_required_intake_forms_for_application(application):
        if IntakeFormResponse.objects.filter(form=form, program_application=application).exists():
            continue
        existing_open = IntakeFormRequest.objects.filter(
            form=form,
            program_application=application,
            status=INTAKE_REQUEST_STATUS_REQUESTED,
            expires_at__gt=timezone.now(),
        ).first()
        if existing_open:
            return existing_open
        return request_intake_form_for_application(
            application,
            actor=actor,
            form=form,
            send_email=True,
            replace_existing=False,
        )
    return None
