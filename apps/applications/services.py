"""Application business logic: duplicate checking, submission, and state machine."""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from django.db.models import BooleanField, Case, Exists, IntegerField, OuterRef, Q, Subquery, Value, When
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE, ENROLLMENT_STATUS_PENDING
from apps.academics.models import Cohort, Course, CourseEnrollment, CourseVersion, DiplomaEnrollment
from apps.academics.services import activate_course_enrollment, activate_diploma_enrollment
from apps.accounts.constants import GROUP_STUDENT, USER_STATUS_INACTIVE
from apps.applications import documents as application_documents
from apps.attendance.services import get_or_create_attendance_code_for_enrollment
from apps.audit.constants import ACTION_APPLICATION_DECISION_REVERTED, ACTION_APPLICATION_STATUS_CHANGE
from apps.audit.services import record_audit_event
from apps.communications.constants import (
    TEMPLATE_ACCEPTANCE,
    TEMPLATE_COURSE_PAYMENT_INSTRUCTIONS,
    TEMPLATE_DIPLOMA_ENTRANCE_EXAM_SCHEDULE,
    TEMPLATE_DIPLOMA_INTERVIEW_REQUEST,
    TEMPLATE_REJECTION,
)
from apps.communications.services import (
    queue_program_application_email,
)
from apps.core.phone import normalize_phone
from apps.entrance_exams.models import EntranceAttempt
from apps.intake_forms import services as intake_services
from apps.intake_forms.constants import (  # noqa: E402
    INTAKE_REQUESTABLE_STATUSES,
)
from apps.intake_forms.models import IntakeForm, IntakeFormResponse
from apps.intake_forms.selectors import (  # noqa: E402
    ACTIVE_COURSE_VERSION_ORDERING,
    REQUIRED_INTAKE_FORM_ORDERING,
    get_intake_requirement_states,
    get_required_intake_forms_for_application,
    has_required_intake_response,
)
from apps.payments import services as payment_services
from apps.payments.constants import PAYMENT_STATUS_PAID, WAIVER_STATUS_ACTIVE
from apps.payments.models import PaymentRecord, PaymentWaiver
from apps.students.identity import (
    COUNTRY_EG,
    normalize_identity_number,
)
from apps.students.models import StudentProfile

from .constants import (
    DECISION_ACTION_ACCEPT,
    DECISION_ACTION_ACCOUNT_ACTIVATED,
    DECISION_ACTION_ACTIVATE,
    DECISION_ACTION_ENTRANCE_SUBMITTED,
    DECISION_ACTION_INVITED_TO_ENTRANCE,
    DECISION_ACTION_PAYMENT_PENDING,
    DECISION_ACTION_PAYMENT_WAIVER_REVOKED,
    DECISION_ACTION_REJECT,
    DECISION_ACTION_REQUEST_INTERVIEW,
    DECISION_ACTION_REVERT,
    DELIVERY_MODE_CHOICES,
    DELIVERY_MODE_ONLINE,
    DOCUMENT_REQUEST_STATUS_CANCELLED,
    DOCUMENT_REQUEST_STATUS_EXPIRED,
    DOCUMENT_REQUEST_STATUS_REQUESTED,
    DOCUMENT_REQUEST_STATUS_SUBMITTED,
    EMAIL_ACTION_ACCEPTANCE,
    EMAIL_ACTION_REJECTION,
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED,
    PROGRAM_APP_STATUS_ACTIVATED,
    PROGRAM_APP_STATUS_DOCUMENTS_REQUESTED,
    PROGRAM_APP_STATUS_DOCUMENTS_SUBMITTED,
    PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED,
    PROGRAM_APP_STATUS_INTERVIEW_REQUESTED,
    PROGRAM_APP_STATUS_INVITED,
    PROGRAM_APP_STATUS_PAYMENT_PENDING,
    PROGRAM_APP_STATUS_READY_FOR_ACTIVATION,
    PROGRAM_APP_STATUS_REJECTED,
    PROGRAM_APP_STATUS_SUBMITTED,
    PROGRAM_APP_STATUS_SURVEY_REQUESTED,
    PROGRAM_APP_STATUS_SURVEY_SUBMITTED,
    PROGRAM_TYPE_COURSE,
    PROGRAM_TYPE_DIPLOMA,
)
from .models import (
    ApplicationDocumentUpload,
    ProgramApplication,
    ProgramApplicationDecision,
    ProgramApplicationEmailLog,
    ProgramApplicationNote,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import User

from apps.accounts.services import send_activation_email

User = get_user_model()

logger = logging.getLogger(__name__)


@dataclass
class RegistrationData:
    """Container for the validated registration payload."""

    email: str
    full_name: str
    date_of_birth: date
    gender: str
    address: str
    phone: str
    national_id: str
    education: str
    specialization: str
    employer: str
    current_job: str
    church: str
    priest_name: str
    priest_contact: str
    country_code: str = COUNTRY_EG
    program_type: str | None = None
    cohort_id: int | None = None
    course_id: int | None = None
    delivery_mode: str = DELIVERY_MODE_ONLINE


def find_duplicate(data: RegistrationData) -> str | None:
    """Return the field name that conflicts, or None when the data is fresh.

    Checks in order: identity number (normalized first, then raw for
    backward compatibility with unnormalized old rows), email, phone.
    """
    normalized_id = normalize_identity_number(data.country_code, data.national_id)
    if normalized_id:
        if StudentProfile.objects.filter(national_id=normalized_id).exists():
            return "national_id"
        if User.objects.filter(national_id=normalized_id).exists():
            return "national_id"
        if data.national_id != normalized_id and (
            StudentProfile.objects.filter(national_id=data.national_id).exists()
            or User.objects.filter(national_id=data.national_id).exists()
        ):
            return "national_id"
    elif data.national_id and (
        StudentProfile.objects.filter(national_id=data.national_id).exists()
        or User.objects.filter(national_id=data.national_id).exists()
    ):
        return "national_id"
    if User.objects.filter(email__iexact=data.email).exists():
        return "email"
    normalized_phone = normalize_phone(data.phone)
    if normalized_phone:
        if StudentProfile.objects.filter(phone=normalized_phone).exists():
            return "phone"
        if User.objects.exclude(phone="").filter(phone=normalized_phone).exists():
            return "phone"
    return None


def is_apply_target_open(cohort=None, course=None) -> bool:
    """Shared open-target predicate for registration and apply flows (A5/S2-08).

    A diploma cohort is open when it is active, its application window has
    not ended, and it has an active entrance exam. A course is open when it
    is active and has an active version. The registration form, the apply
    selectors, and the submission services must all use this single rule.
    """
    today = timezone.now().date()
    if cohort is not None:
        if not cohort.is_active:
            return False
        if cohort.end_date is None or cohort.end_date < today:
            return False
        return cohort.entrance_exams.filter(is_active=True).exists()
    if course is not None:
        return bool(course.is_active and course.versions.filter(is_active=True).exists())
    return False


@transaction.atomic
def submit_registration(
    data: RegistrationData,
) -> tuple[StudentProfile, ProgramApplication | None]:
    """Create a user, profile, and optional program application.

    Returns (StudentProfile, ProgramApplication | None).
    Raises ValueError with a duplicate field name if the data conflicts.
    """
    duplicate_on = find_duplicate(data)
    if duplicate_on:
        raise ValueError(duplicate_on)

    # Random, never-derived initial password: it is never delivered to the
    # user (S2-24); the password is established during account activation.
    user_password = secrets.token_urlsafe(16)
    normalized_id = normalize_identity_number(data.country_code, data.national_id)
    normalized_phone = normalize_phone(data.phone)
    # Students never store phone on User; StudentProfile.phone is the single
    # owner (created below).
    user = User.objects.create_user(
        email=data.email,
        password=user_password,
        full_name=data.full_name,
        country_code=data.country_code,
        national_id=normalized_id,
    )
    user.is_active = False
    user.status = USER_STATUS_INACTIVE
    user.save(update_fields=["is_active", "status", "updated_at"])
    user.groups.add(_student_group())

    profile = StudentProfile.objects.create(
        user=user,
        full_name=data.full_name,
        date_of_birth=data.date_of_birth,
        gender=data.gender,
        address=data.address,
        phone=normalized_phone,
        country_code=data.country_code,
        national_id=normalized_id,
        education=data.education,
        specialization=data.specialization,
        employer=data.employer,
        current_job=data.current_job,
        church=data.church,
        priest_name=data.priest_name,
        priest_contact=data.priest_contact,
    )

    program_application = None
    if data.program_type == PROGRAM_TYPE_DIPLOMA and data.cohort_id:
        if data.delivery_mode not in dict(DELIVERY_MODE_CHOICES):
            raise ValueError(_("Please select an attendance mode."))
        cohort = Cohort.objects.filter(pk=data.cohort_id).first()
        if cohort is None or not is_apply_target_open(cohort=cohort):
            raise ValueError(_("This program is no longer accepting applications."))
        program_application = ProgramApplication.objects.create(
            student=profile,
            program_type=PROGRAM_TYPE_DIPLOMA,
            cohort=cohort,
            delivery_mode=data.delivery_mode,
            status=PROGRAM_APP_STATUS_SUBMITTED,
        )
    elif data.program_type == PROGRAM_TYPE_COURSE and data.course_id:
        course = Course.objects.filter(pk=data.course_id).first()
        if course is None or not is_apply_target_open(course=course):
            raise ValueError(_("This course is no longer accepting applications."))
        program_application = ProgramApplication.objects.create(
            student=profile,
            program_type=PROGRAM_TYPE_COURSE,
            course=course,
            status=PROGRAM_APP_STATUS_SUBMITTED,
        )

    transaction.on_commit(lambda: _send_activation_email_safely(user))

    return profile, program_application


def _send_activation_email_safely(user) -> None:
    """Keep a committed registration successful if email configuration fails."""
    try:
        send_activation_email(user, idempotency_key=f"activation:new:{user.pk}")
    except Exception:  # noqa: BLE001
        logger.exception("Failed to queue activation email for user_id=%s", user.pk)


def submit_program_application(
    profile: StudentProfile,
    program_type: str,
    cohort: Cohort | None = None,
    course: Course | None = None,
    delivery_mode: str = DELIVERY_MODE_ONLINE,
) -> ProgramApplication:
    """Create a new program application for an existing student."""
    if program_type == PROGRAM_TYPE_COURSE:
        if course is None or not is_apply_target_open(course=course):
            raise ValueError(_("This course has no active version available for enrollment."))
    elif program_type == PROGRAM_TYPE_DIPLOMA:
        if cohort is None or not is_apply_target_open(cohort=cohort):
            raise ValueError(_("This program is no longer accepting applications."))
        if delivery_mode not in dict(DELIVERY_MODE_CHOICES):
            raise ValueError(_("Please select an attendance mode."))
    _check_duplicate_program_application(profile, program_type, cohort=cohort, course=course)

    return ProgramApplication.objects.create(
        student=profile,
        program_type=program_type,
        cohort=cohort,
        course=course,
        delivery_mode=delivery_mode if program_type == PROGRAM_TYPE_DIPLOMA else DELIVERY_MODE_ONLINE,
        status=PROGRAM_APP_STATUS_SUBMITTED,
    )


def _check_duplicate_program_application(
    profile: StudentProfile,
    program_type: str,
    cohort: Cohort | None = None,
    course: Course | None = None,
) -> None:
    """Raise ValueError if the student already has a pending/active program for the same target."""
    if program_type == PROGRAM_TYPE_DIPLOMA and cohort is not None:
        if not isinstance(cohort, Cohort):
            cohort = Cohort.objects.get(pk=cohort)
        existing = ProgramApplication.objects.filter(
            student=profile,
            program_type=PROGRAM_TYPE_DIPLOMA,
            cohort=cohort,
        ).exclude(
            status=PROGRAM_APP_STATUS_REJECTED,
        ).exists()
        if existing:
            raise ValueError(
                _("You already have a pending or active application for this diploma cohort.")
            )

    elif program_type == PROGRAM_TYPE_COURSE and course is not None:
        if not isinstance(course, Course):
            course = Course.objects.get(pk=course)
        existing = ProgramApplication.objects.filter(
            student=profile,
            program_type=PROGRAM_TYPE_COURSE,
            course=course,
        ).exclude(
            status=PROGRAM_APP_STATUS_REJECTED,
        ).exists()
        if existing:
            raise ValueError(
                _("You already have a pending or active application for this course.")
            )


def _student_group():
    group, _created = Group.objects.get_or_create(name=GROUP_STUDENT)
    return group


# ---------------------------------------------------------------------------
# Transition services
# ---------------------------------------------------------------------------


def _record_decision(application, action, old_status, new_status, actor, reason: str = "", metadata: dict | None = None) -> ProgramApplicationDecision:
    return ProgramApplicationDecision.objects.create(
        program_application=application,
        action=action,
        old_status=old_status,
        new_status=new_status,
        actor=actor,
        reason=reason,
        metadata=metadata or {},
    )


def _set_status(application, new_status):
    application.status = new_status
    application.save(update_fields=["status", "updated_at"])


@transaction.atomic
def mark_application_account_activated(application, actor=None) -> None:
    """Transition submitted -> account_activated."""
    locked = ProgramApplication.objects.select_for_update().get(pk=application.pk)
    if locked.status != PROGRAM_APP_STATUS_SUBMITTED:
        raise ValueError(_("Only submitted applications can be marked account_activated."))
    old_status = locked.status
    _set_status(locked, PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED)
    _record_decision(locked, DECISION_ACTION_ACCOUNT_ACTIVATED, old_status, PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED, actor)
    record_audit_event(
        actor=actor,
        action="application.account_activated",
        target_type="program_application",
        target_id=str(locked.pk),
        metadata={"old_status": old_status},
    )


def _has_non_resend_email_log(application, action) -> bool:
    return ProgramApplicationEmailLog.objects.filter(
        program_application=application,
        action=action,
        status__in=["sent", "queued"],
        is_resend=False,
    ).exists()


@transaction.atomic
def send_post_activation_application_email(application, actor=None, force_resend=False) -> None:
    """Send the correct post-activation email for course or diploma, then advance status.

    Course -> payment_pending
    Diploma (initial) -> invited_to_entrance
    Diploma (post-survey) -> ready_for_activation

    Emails are sent via on_commit so they only fire after the transaction commits.
    """
    locked = ProgramApplication.objects.select_for_update().get(pk=application.pk)
    allowed = [
        PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED,
        PROGRAM_APP_STATUS_ACCEPTED,
        PROGRAM_APP_STATUS_PAYMENT_PENDING,
    ]
    allowed.append(PROGRAM_APP_STATUS_SURVEY_SUBMITTED)
    if locked.status not in allowed:
        raise ValueError(_("Application must be in account_activated status to send post-activation email."))

    # Diploma applications still have to enter the entrance-assessment path
    # after account activation. Their document request is issued after the
    # admissions decision; only then may a required intake form be requested.
    diploma_initial_activation = (
        locked.program_type == PROGRAM_TYPE_DIPLOMA
        and locked.status == PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED
    )
    # Course applications wait for the admissions decision before the survey
    # is requested. Diploma applications use this initial account-activation
    # hook only to enter the entrance-assessment path.
    course_pre_decision = (
        locked.program_type == PROGRAM_TYPE_COURSE
        and locked.status == PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED
    )
    if not has_required_intake_response(locked) and not diploma_initial_activation and not course_pre_decision:
        intake_services.ensure_required_intake_request(locked, actor=actor)
        return
    if course_pre_decision:
        return

    if locked.program_type == PROGRAM_TYPE_COURSE:
        email_action = "course_payment_instructions"
        if not force_resend and _has_non_resend_email_log(locked, email_action):
            return
        transaction.on_commit(lambda: _send_course_payment_instructions_email(locked, actor))
        _advance_to(locked, PROGRAM_APP_STATUS_PAYMENT_PENDING, DECISION_ACTION_PAYMENT_PENDING, actor)

    elif locked.program_type == PROGRAM_TYPE_DIPLOMA:
        if locked.status == PROGRAM_APP_STATUS_SURVEY_SUBMITTED:
            _advance_to(locked, PROGRAM_APP_STATUS_PAYMENT_PENDING, DECISION_ACTION_PAYMENT_PENDING, actor)
            return
        email_action = "diploma_entrance_exam_schedule"
        if not force_resend and _has_non_resend_email_log(locked, email_action):
            return
        transaction.on_commit(lambda: _send_diploma_entrance_schedule_email(locked, actor))
        _advance_to(locked, PROGRAM_APP_STATUS_INVITED, DECISION_ACTION_INVITED_TO_ENTRANCE, actor)


def _advance_to(application, new_status, decision_action, actor):
    old_status = application.status
    _set_status(application, new_status)
    _record_decision(application, decision_action, old_status, new_status, actor)
    record_audit_event(
        actor=actor,
        action=f"application.{new_status}",
        target_type="program_application",
        target_id=str(application.pk),
        metadata={"old_status": old_status, "new_status": new_status, "decision_action": decision_action},
    )


def _send_course_payment_instructions_email(prog_app, actor=None) -> None:
    email = prog_app.student.user.email
    if not email:
        return
    context = {
        "full_name": prog_app.student.full_name or "",
        "course_title": prog_app.course.title if prog_app.course else "",
        "staff_phone": "",
    }
    queue_program_application_email(
        program_application=prog_app,
        action="course_payment_instructions",
        template_slug=TEMPLATE_COURSE_PAYMENT_INSTRUCTIONS,
        to_email=email,
        context=context,
        sent_by=actor,
        idempotency_key=f"application:course_payment_instructions:app:{prog_app.pk}",
        reference=f"app:{prog_app.pk}",
    )


def _send_diploma_entrance_schedule_email(prog_app, actor=None) -> None:
    email = prog_app.student.user.email
    if not email:
        return
    cohort = prog_app.cohort
    exam = cohort.entrance_exams.filter(is_active=True).first() if cohort else None
    context = {
        "full_name": prog_app.student.full_name or "",
        "diploma_title": cohort.diploma.title if cohort and cohort.diploma else "",
        "cohort_name": cohort.name if cohort else "",
        "exam_title": exam.title if exam else "",
        "exam_open_at": "",
        "exam_close_at": "",
    }
    # ponytail: no exam URL in context per Phase 2 rule
    queue_program_application_email(
        program_application=prog_app,
        action="diploma_entrance_exam_schedule",
        template_slug=TEMPLATE_DIPLOMA_ENTRANCE_EXAM_SCHEDULE,
        to_email=email,
        context=context,
        sent_by=actor,
        idempotency_key=f"application:diploma_entrance_schedule:app:{prog_app.pk}",
        reference=f"app:{prog_app.pk}",
    )


@transaction.atomic
def mark_entrance_submitted(application, actor=None, attempt=None) -> None:
    """Transition invited_to_entrance -> entrance_submitted.
    
    Also accepts submitted for backward compat with pre-Phase-2 fixtures.
    """
    locked = ProgramApplication.objects.select_for_update().get(pk=application.pk)
    if locked.status not in (PROGRAM_APP_STATUS_INVITED, PROGRAM_APP_STATUS_SUBMITTED):
        raise ValueError(_("Only invited applications can submit entrance exam."))
    old_status = locked.status
    _set_status(locked, PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED)
    metadata = {}
    if attempt:
        metadata["attempt_id"] = attempt.pk
    _record_decision(
        locked, DECISION_ACTION_ENTRANCE_SUBMITTED,
        old_status, PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED, actor,
        metadata=metadata,
    )
    record_audit_event(
        actor=actor,
        action="application.entrance_submitted",
        target_type="program_application",
        target_id=str(locked.pk),
        metadata={"old_status": old_status, **(metadata or {})},
    )


def invite_to_entrance_exam(application, actor) -> None:
    """Transition an activated diploma application to entrance invitation."""
    locked = ProgramApplication.objects.select_for_update().get(pk=application.pk)
    if ACTION_INVITE_TO_ENTRANCE not in allowed_application_actions(locked):
        raise ValueError(_("Application must be awaiting admin review before invitation."))
    if locked.program_type != PROGRAM_TYPE_DIPLOMA:
        raise ValueError(_("Only diploma applications can be invited to entrance exam."))
    _advance_to(locked, PROGRAM_APP_STATUS_INVITED, DECISION_ACTION_INVITED_TO_ENTRANCE, actor)


# ---------------------------------------------------------------------------
# Intake request helper
# ---------------------------------------------------------------------------


def _attempt_intake_request_safe(application, actor=None) -> str | None:
    """Create intake request if needed. Returns None on success/expected skip,
    or an error reason string if an unexpected failure occurred.

    Unexpected failures are also logged and recorded as system notes so
    admins can see why activation remains blocked.
    """
    try:
        intake_services.ensure_required_intake_request(application, actor=actor)
    except ValueError as exc:
        logger.warning("Intake request not created for app %s: %s", application.pk, exc)
    except Exception as exc:
        logger.exception("Intake request error for app %s: %s", application.pk, exc)
        reason = _("Intake request creation failed: {error}").format(error=exc)
        record_program_application_note(
            application,
            actor=None,
            note=reason,
            metadata={"intake_request_error": str(exc)},
        )
        return reason
    return None


# ---------------------------------------------------------------------------
# Decision services
# ---------------------------------------------------------------------------


@transaction.atomic
def accept_program_application(application, actor, notes: str = "", send_email: bool = False) -> None:
    """Accept a course or diploma without requiring an entrance score decision."""
    locked = ProgramApplication.objects.select_for_update().get(pk=application.pk)
    if ACTION_ACCEPT not in allowed_application_actions(locked):
        raise ValueError(_("Application cannot be accepted from current status."))
    if locked.program_type == PROGRAM_TYPE_COURSE:
        allowed = {PROGRAM_APP_STATUS_SUBMITTED, PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED, PROGRAM_APP_STATUS_PAYMENT_PENDING}
    else:
        allowed = {
            PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED,
            PROGRAM_APP_STATUS_INTERVIEW_REQUESTED,
        }
    if locked.status not in allowed:
        raise ValueError(_("Application cannot be accepted from current status."))
    old_status = locked.status
    locked.accept(actor, notes=notes)
    _record_decision(locked, DECISION_ACTION_ACCEPT, old_status, PROGRAM_APP_STATUS_ACCEPTED, actor)
    record_audit_event(
        actor=actor,
        action="application.approved",
        target_type="program_application",
        target_id=str(locked.pk),
        metadata={"student_id": locked.student_id, "send_email": send_email},
    )
    if send_email:
        transaction.on_commit(lambda: _send_program_app_acceptance_email(locked, actor=actor))

    # Trigger intake request for course applications that are already paid
    if locked.program_type == PROGRAM_TYPE_COURSE:
        if has_application_paid_record(locked):
            _attempt_intake_request_safe(locked, actor=actor)
        else:
            send_post_activation_application_email(locked, actor=actor)


@transaction.atomic
def reject_program_application(application, actor, reason: str, notes: str = "", send_email: bool = False) -> None:
    """Reject a course or diploma from any active pre-decision admissions state."""
    locked = ProgramApplication.objects.select_for_update().get(pk=application.pk)
    if ACTION_REJECT not in allowed_application_actions(locked):
        raise ValueError(_("Application cannot be rejected from current status."))
    if not reason.strip():
        raise ValueError(_("Rejection reason is required."))
    allowed = {
        PROGRAM_APP_STATUS_INVITED,
        PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED,
        PROGRAM_APP_STATUS_INTERVIEW_REQUESTED,
    }
    if locked.program_type == PROGRAM_TYPE_COURSE:
        allowed |= {PROGRAM_APP_STATUS_SUBMITTED, PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED, PROGRAM_APP_STATUS_PAYMENT_PENDING}
    if locked.status not in allowed:
        raise ValueError(_("Application cannot be rejected from current status."))
    old_status = locked.status
    locked.reject(actor, reason=reason, notes=notes)
    _record_decision(locked, DECISION_ACTION_REJECT, old_status, PROGRAM_APP_STATUS_REJECTED, actor, reason=reason)
    record_audit_event(
        actor=actor,
        action="application.rejected",
        target_type="program_application",
        target_id=str(locked.pk),
        metadata={"reason": reason, "student_id": locked.student_id, "send_email": send_email},
    )
    if send_email:
        transaction.on_commit(lambda: _send_program_app_rejection_email(locked, reason, actor=actor))


@transaction.atomic
def request_interview_program_application(application, actor, *, interview_at=None, phone_number="", expires_at, notes="", send_email=True) -> None:
    """Transition entrance_submitted -> interview_requested."""
    locked = ProgramApplication.objects.select_for_update().get(pk=application.pk)
    if ACTION_REQUEST_INTERVIEW not in allowed_application_actions(locked):
        raise ValueError(_("Interview can only be requested for applications with submitted entrance exam."))
    if locked.status != PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED:
        raise ValueError(_("Interview can only be requested for applications with submitted entrance exam."))
    if locked.program_type != PROGRAM_TYPE_DIPLOMA:
        raise ValueError(_("Interview can only be requested for diploma applications."))
    if not interview_at and not phone_number:
        raise ValueError(_("Either interview date/time or staff phone instructions is required."))
    if not expires_at:
        raise ValueError(_("Expiry time is required for interview request."))
    if timezone.is_naive(expires_at):
        raise ValueError(_("Expiry time must be timezone-aware."))
    if expires_at <= timezone.now():
        raise ValueError(_("Expiry time must be in the future."))

    old_status = locked.status
    _set_status(locked, PROGRAM_APP_STATUS_INTERVIEW_REQUESTED)
    metadata = {
        "interview_at": interview_at.isoformat() if interview_at else None,
        "phone_number": phone_number,
        "expires_at": expires_at.isoformat(),
    }
    _record_decision(
        locked, DECISION_ACTION_REQUEST_INTERVIEW,
        old_status, PROGRAM_APP_STATUS_INTERVIEW_REQUESTED, actor,
        metadata=metadata,
    )
    if notes:
        record_program_application_note(locked, actor, note=notes, metadata=metadata)
    record_audit_event(
        actor=actor,
        action="application.interview_requested",
        target_type="program_application",
        target_id=str(locked.pk),
        metadata=metadata,
    )
    if send_email:
        transaction.on_commit(lambda: _send_interview_request_email(locked, metadata))


def _send_interview_request_email(prog_app, metadata: dict) -> None:
    email = prog_app.student.user.email
    if not email:
        return
    context = {
        "full_name": prog_app.student.full_name or "",
        "diploma_title": prog_app.cohort.diploma.title if prog_app.cohort and prog_app.cohort.diploma else "",
        "cohort_name": prog_app.cohort.name if prog_app.cohort else "",
        "interview_at": metadata.get("interview_at", ""),
        "phone_number": metadata.get("phone_number", ""),
        "expires_at": metadata.get("expires_at", ""),
    }
    queue_program_application_email(
        program_application=prog_app,
        action="diploma_interview_request",
        template_slug=TEMPLATE_DIPLOMA_INTERVIEW_REQUEST,
        to_email=email,
        context=context,
        sent_by=None,
        idempotency_key=f"application:interview_request:app:{prog_app.pk}:{metadata.get('interview_at') or ''}",
        reference=f"app:{prog_app.pk}",
    )


@transaction.atomic
def revert_program_application_decision(application, actor, reason: str = "") -> None:
    locked = ProgramApplication.objects.select_for_update().get(pk=application.pk)
    if ACTION_REVERT not in allowed_application_actions(locked):
        raise ValueError(_("This application decision cannot be reverted in its current state."))
    if locked.entrance_attempts.exists():
        raise ValueError(_("You cannot revert an application after an entrance exam attempt exists."))

    last_decision = locked.decisions.exclude(action=DECISION_ACTION_REVERT).order_by("-created_at").first()
    if not last_decision:
        raise ValueError(_("No decision to revert."))

    with transaction.atomic():
        old_status = locked.status
        locked.status = last_decision.old_status
        locked.reviewed_by = actor
        locked.reviewed_at = timezone.now()
        locked.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
        _record_decision(locked, DECISION_ACTION_REVERT, old_status, locked.status, actor, reason=reason)
        record_audit_event(
            actor=actor,
            action=ACTION_APPLICATION_DECISION_REVERTED,
            target_type="program_application",
            target_id=str(locked.pk),
            metadata={"student_id": locked.student_id, "reason": reason},
        )


ENTRANCE_DECISION_STATUSES = frozenset({
    PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED,
    PROGRAM_APP_STATUS_INTERVIEW_REQUESTED,
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_REJECTED,
})


@transaction.atomic
def change_entrance_decision(
    application,
    *,
    target: str,
    actor,
    reason: str = "",
    notes: str = "",
    send_email: bool = False,
    interview_at=None,
    phone_number: str = "",
    expires_at=None,
) -> None:
    """Change an entrance decision while the application is still in a decision state.

    Allowed while status is entrance_submitted / interview_requested / accepted /
    rejected. Once the application advances past the decision (documents, survey,
    activation), the decision is locked and this raises ValueError. The academic
    entrance score is never rewritten by a decision change.
    """
    locked = ProgramApplication.objects.select_for_update().get(pk=application.pk)
    if locked.status not in ENTRANCE_DECISION_STATUSES:
        raise ValueError(_("The decision can no longer be changed for this application."))
    if target not in ENTRANCE_DECISION_STATUSES:
        raise ValueError(_("Invalid decision target."))
    if target == locked.status:
        raise ValueError(_("The application already has this decision."))
    if target == PROGRAM_APP_STATUS_REJECTED and not (reason or "").strip():
        raise ValueError(_("Rejection reason is required."))
    if target == PROGRAM_APP_STATUS_INTERVIEW_REQUESTED:
        if locked.program_type != PROGRAM_TYPE_DIPLOMA:
            raise ValueError(_("Interview can only be requested for diploma applications."))
        if not interview_at and not phone_number:
            raise ValueError(_("Either interview date/time or staff phone instructions is required."))
        if not expires_at:
            raise ValueError(_("Expiry time is required for interview request."))
        if timezone.is_naive(expires_at):
            raise ValueError(_("Expiry time must be timezone-aware."))
        if expires_at <= timezone.now():
            raise ValueError(_("Expiry time must be in the future."))

    old_status = locked.status
    decision_action = {
        PROGRAM_APP_STATUS_ACCEPTED: DECISION_ACTION_ACCEPT,
        PROGRAM_APP_STATUS_REJECTED: DECISION_ACTION_REJECT,
        PROGRAM_APP_STATUS_INTERVIEW_REQUESTED: DECISION_ACTION_REQUEST_INTERVIEW,
        PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED: DECISION_ACTION_REVERT,
    }[target]
    audit_action = {
        PROGRAM_APP_STATUS_ACCEPTED: "application.approved",
        PROGRAM_APP_STATUS_REJECTED: "application.rejected",
        PROGRAM_APP_STATUS_INTERVIEW_REQUESTED: "application.interview_requested",
        PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED: ACTION_APPLICATION_DECISION_REVERTED,
    }[target]
    metadata = {}
    if target == PROGRAM_APP_STATUS_INTERVIEW_REQUESTED:
        metadata = {
            "interview_at": interview_at.isoformat() if interview_at else None,
            "phone_number": phone_number,
            "expires_at": expires_at.isoformat(),
        }
    locked.status = target
    locked.reviewed_by = actor
    locked.reviewed_at = timezone.now()
    if notes:
        locked.admin_notes = notes
    locked.save(update_fields=["status", "reviewed_by", "reviewed_at", "admin_notes", "updated_at"])
    _record_decision(locked, decision_action, old_status, target, actor, reason=reason, metadata=metadata)
    if notes:
        record_program_application_note(locked, actor, note=notes, metadata=metadata)
    record_audit_event(
        actor=actor,
        action=audit_action,
        target_type="program_application",
        target_id=str(locked.pk),
        metadata={"old_status": old_status, "new_status": target, "reason": reason, "send_email": send_email, **metadata},
    )
    if send_email:
        if target == PROGRAM_APP_STATUS_ACCEPTED:
            transaction.on_commit(lambda: _send_program_app_acceptance_email(locked, actor=actor))
        elif target == PROGRAM_APP_STATUS_REJECTED:
            transaction.on_commit(lambda: _send_program_app_rejection_email(locked, reason, actor=actor))
        elif target == PROGRAM_APP_STATUS_INTERVIEW_REQUESTED:
            transaction.on_commit(lambda: _send_interview_request_email(locked, metadata))


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------


def record_program_application_note(application, actor, note: str, metadata: dict | None = None) -> ProgramApplicationNote:
    # ponytail: actor may be None for automated system notes (e.g. entrance snapshot finalization).
    return ProgramApplicationNote.objects.create(
        program_application=application,
        actor=actor,
        note=note,
        metadata={"system": actor is None, **(metadata or {})},
    )


# ---------------------------------------------------------------------------
# Activation hook
# ---------------------------------------------------------------------------


@transaction.atomic
def handle_user_account_activated(user, actor=None) -> None:
    """Find open submitted applications and advance them after account activation.

    Idempotent: if application is already past submitted, does nothing.
    Runs inside the caller's activation transaction (S2-31); the submitted
    rows are locked so a concurrent application update cannot race the
    transition, and a failure in one application's transition rolls back all.
    """
    if not hasattr(user, "student_profile"):
        return
    profile = user.student_profile
    applications = ProgramApplication.objects.select_for_update().filter(
        student=profile,
        status=PROGRAM_APP_STATUS_SUBMITTED,
    )
    for application in applications:
        mark_application_account_activated(application, actor=actor)
        send_post_activation_application_email(application, actor=actor)


# ---------------------------------------------------------------------------
# Activation Gate — Phase 3
# ---------------------------------------------------------------------------

@dataclass
class ActivationCheck:
    ready: bool
    reasons: list[str] = field(default_factory=list)


@dataclass
class ActivationResult:
    activated: bool
    enrollment: object | None = None
    reasons: list[str] = field(default_factory=list)


def has_application_documents_submitted(application) -> bool:
    """Check if a complete document upload exists for this application."""
    cached = getattr(application, "_has_submitted_documents", None)
    if cached is not None:
        return bool(cached)
    return application_documents.has_application_documents_submitted(application)


def has_application_paid_record(application) -> bool:
    """Check if a paid, non-deleted PaymentRecord exists for this application."""
    cached = getattr(application, "_has_paid_record", None)
    if cached is not None:
        return bool(cached)
    return PaymentRecord.objects.filter(
        application=application,
        status=PAYMENT_STATUS_PAID,
        deleted_at__isnull=True,
    ).exists()


def has_active_application_waiver(application) -> bool:
    """Check if an active Admin payment waiver exists for this application."""
    cached = getattr(application, "_has_active_waiver", None)
    if cached is not None:
        return bool(cached)
    return PaymentWaiver.objects.filter(
        application=application,
        status=WAIVER_STATUS_ACTIVE,
    ).exists()


def has_application_payment_readiness(application) -> bool:
    """Payment readiness: satisfied payment OR a valid recorded Admin waiver."""
    return has_application_paid_record(application) or has_active_application_waiver(application)


def has_active_course_version(application) -> bool:
    """Return whether a Course application has an active version to enroll."""
    cached = getattr(application, "_has_active_course_version", None)
    if cached is not None:
        return bool(cached)
    return bool(
        application.course_id
        and CourseVersion.objects.filter(course_id=application.course_id, is_active=True).exists()
    )


def _active_application_waiver_id(application) -> str | None:
    waiver = PaymentWaiver.objects.filter(
        application=application,
        status=WAIVER_STATUS_ACTIVE,
    ).first()
    return str(waiver.pk) if waiver else None


def get_active_application_waiver(application):
    """Return the active Admin payment waiver for this application, or None."""
    return PaymentWaiver.objects.filter(
        application=application,
        status=WAIVER_STATUS_ACTIVE,
    ).first()


def can_activate_application(application) -> ActivationCheck:
    """Determine whether an application is ready for activation.

    This is a read-only check — no mutations.
    Phase 5: required intake form check added.
    """
    if application.status == PROGRAM_APP_STATUS_ACTIVATED:
        return ActivationCheck(ready=False, reasons=["already_activated"])

    reasons: list[str] = []

    if not application.student.user.email_activated_at:
        reasons.append("account_not_activated")

    if application.program_type == PROGRAM_TYPE_COURSE:
        if application.status not in (PROGRAM_APP_STATUS_ACCEPTED, PROGRAM_APP_STATUS_PAYMENT_PENDING):
            reasons.append("not_accepted")
        if not application.course:
            reasons.append("no_course_target")
        elif not has_active_course_version(application):
            reasons.append("no_active_course_version")
        if not has_application_payment_readiness(application):
            reasons.append("not_paid")
        # Phase 5: required intake check
        if not _has_required_intake_check(application):
            reasons.append("intake_form_required")
        return ActivationCheck(ready=len(reasons) == 0, reasons=reasons)

    # Diploma
    if application.status not in (
        PROGRAM_APP_STATUS_DOCUMENTS_SUBMITTED,
        PROGRAM_APP_STATUS_PAYMENT_PENDING,
        PROGRAM_APP_STATUS_READY_FOR_ACTIVATION,
    ):
        reasons.append("not_accepted_or_documents_not_submitted")
    if not has_application_documents_submitted(application):
        reasons.append("documents_not_uploaded")
    if not application.cohort:
        reasons.append("no_cohort_target")
    if not has_application_payment_readiness(application):
        reasons.append("not_paid")
    # Phase 5: required intake check
    if not _has_required_intake_check(application):
        reasons.append("intake_form_required")

    return ActivationCheck(ready=len(reasons) == 0, reasons=reasons)


def _has_required_intake_check(application) -> bool:
    """Check if required intake form is satisfied. True if no form or response exists."""
    cached = getattr(application, "_has_required_intake_response", None)
    if cached is not None:
        return bool(cached)
    return has_required_intake_response(application)


@transaction.atomic
def activate_application_if_ready(application, actor=None) -> ActivationResult:
    """Check readiness and create/activate enrollment if all gates pass.

    Must be the only path that creates enrollment from an application.
    Idempotent: if already activated, return existing enrollment.
    """
    # Lock the application row first, then check state
    locked = ProgramApplication.objects.select_for_update().get(pk=application.pk)

    # Guard: already activated (re-check under lock for concurrency safety)
    if locked.status == PROGRAM_APP_STATUS_ACTIVATED:
        existing = _find_existing_enrollment(locked)
        return ActivationResult(
            activated=True,
            enrollment=existing,
            reasons=["already_activated"],
        )

    check = can_activate_application(locked)
    if not check.ready:
        return ActivationResult(activated=False, reasons=check.reasons)

    if locked.program_type == PROGRAM_TYPE_COURSE:
        cv = locked.course.versions.filter(is_active=True).order_by("-created_at", "-id").first()
        if cv is None:
            return ActivationResult(activated=False, reasons=["no_active_course_version"])
        enrollment, created = CourseEnrollment.objects.get_or_create(
            student=locked.student,
            course_version=cv,
            defaults={"status": ENROLLMENT_STATUS_PENDING},
        )
        enrollment_status_before = enrollment.status
        enrollment_activated_at_before = enrollment.activated_at.isoformat() if enrollment.activated_at else None
        enrollment_expires_at_before = enrollment.expires_at.isoformat() if enrollment.expires_at else None
        if enrollment.status != ENROLLMENT_STATUS_ACTIVE:
            activate_course_enrollment(enrollment)
        enrollment_type = "course"
        enrollment_id = enrollment.pk
    else:
        # Diploma enrollment
        enrollment, created = DiplomaEnrollment.objects.get_or_create(
            student=locked.student,
            cohort=locked.cohort,
            defaults={"status": ENROLLMENT_STATUS_PENDING},
        )
        enrollment_status_before = enrollment.status
        enrollment_activated_at_before = enrollment.activated_at.isoformat() if enrollment.activated_at else None
        enrollment_expires_at_before = enrollment.expires_at.isoformat() if enrollment.expires_at else None
        if enrollment.status != ENROLLMENT_STATUS_ACTIVE:
            activate_diploma_enrollment(enrollment)
        enrollment_type = "diploma"
        enrollment_id = enrollment.pk

    # Generate attendance code for the new enrollment
    get_or_create_attendance_code_for_enrollment(enrollment, actor=actor)

    # Update application status
    old_status = locked.status
    locked.status = PROGRAM_APP_STATUS_ACTIVATED
    locked.save(update_fields=["status", "updated_at"])

    # Record decision
    _record_decision(
        locked,
        DECISION_ACTION_ACTIVATE,
        old_status,
        PROGRAM_APP_STATUS_ACTIVATED,
        actor,
        metadata={
            "payment_record_id": _find_paid_record_id(locked),
            "payment_waiver_id": _active_application_waiver_id(locked),
            "enrollment_created": created,
            "enrollment_changed_by_activation": enrollment_status_before != ENROLLMENT_STATUS_ACTIVE,
            "enrollment_status_before": enrollment_status_before,
            "enrollment_activated_at_before": enrollment_activated_at_before,
            "enrollment_expires_at_before": enrollment_expires_at_before,
            "enrollment_id": enrollment_id,
            "enrollment_type": enrollment_type,
            "activated_by": actor.email if actor else "",
        },
    )

    # Record audit event
    record_audit_event(
        actor=actor,
        action="application.activated",
        target_type="program_application",
        target_id=str(locked.pk),
        metadata={
            "student_id": locked.student_id,
            "enrollment_id": enrollment_id,
            "enrollment_type": enrollment_type,
        },
    )

    return ActivationResult(
        activated=True,
        enrollment=enrollment,
        reasons=[],
    )


def _restore_activation_enrollment(metadata: dict, *, activation_created_at=None):
    """Restore enrollment state only when this activation changed it.

    New activation decisions persist the exact pre-activation fields. Older
    decisions retain the previous created-enrollment fallback for compatibility.
    """
    enrollment_id = metadata.get("enrollment_id")
    enrollment_type = metadata.get("enrollment_type")
    should_restore = metadata.get("enrollment_changed_by_activation")
    has_prior_snapshot = "enrollment_status_before" in metadata
    if not enrollment_id:
        return None

    if enrollment_type == "diploma":
        enrollment = DiplomaEnrollment.objects.select_for_update().filter(pk=enrollment_id).first()
    elif enrollment_type == "course":
        enrollment = CourseEnrollment.objects.select_for_update().filter(pk=enrollment_id).first()
    else:
        enrollment = None
    if enrollment is None:
        return None
    if should_restore is None:
        if "enrollment_created" in metadata:
            should_restore = bool(metadata.get("enrollment_created"))
        elif activation_created_at is not None:
            should_restore = enrollment.created_at >= activation_created_at - timedelta(seconds=10)
        else:
            should_restore = False
    if not should_restore:
        return None
    if enrollment.status != ENROLLMENT_STATUS_ACTIVE:
        raise ValueError(_(
            "The enrollment changed after activation; access correction was not applied."
        ))

    if has_prior_snapshot:
        enrollment.status = metadata.get("enrollment_status_before") or ENROLLMENT_STATUS_PENDING
        try:
            enrollment.activated_at = (
                datetime.fromisoformat(metadata["enrollment_activated_at_before"])
                if metadata.get("enrollment_activated_at_before")
                else None
            )
            enrollment.expires_at = (
                datetime.fromisoformat(metadata["enrollment_expires_at_before"])
                if metadata.get("enrollment_expires_at_before")
                else None
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(_(
                "The activation history is invalid; access correction was not applied."
            )) from exc
    else:
        enrollment.status = ENROLLMENT_STATUS_PENDING
        enrollment.activated_at = None
        enrollment.expires_at = None
    enrollment.save(update_fields=["status", "activated_at", "expires_at", "updated_at"])
    return enrollment


@transaction.atomic
def rollback_waiver_activation(application_id, waiver_id, actor=None, reason: str = "") -> dict:
    """Undo activation caused solely by a payment waiver that was revoked.

    A real payment always wins: if one exists, revoking an otherwise redundant
    waiver must not remove access. The activation decision metadata identifies
    whether this exact waiver caused activation, so unrelated later/manual
    activation is never rolled back.
    """
    locked = ProgramApplication.objects.select_for_update().get(pk=application_id)
    if locked.status != PROGRAM_APP_STATUS_ACTIVATED:
        return {
            "rolled_back": False,
            "application_status_before": locked.status,
            "application_status_after": locked.status,
            "enrollment_reset": False,
        }
    if has_application_paid_record(locked):
        return {
            "rolled_back": False,
            "application_status_before": locked.status,
            "application_status_after": locked.status,
            "enrollment_reset": False,
        }

    activation = locked.decisions.filter(
        action=DECISION_ACTION_ACTIVATE,
    ).order_by("-created_at", "-id").first()
    metadata = activation.metadata if activation else {}
    if not activation or str(metadata.get("payment_waiver_id") or "") != str(waiver_id):
        return {
            "rolled_back": False,
            "application_status_before": locked.status,
            "application_status_after": locked.status,
            "enrollment_reset": False,
        }
    if metadata.get("enrollment_changed_by_activation") is False:
        return {
            "rolled_back": False,
            "application_status_before": locked.status,
            "application_status_after": locked.status,
            "enrollment_reset": False,
        }

    old_status = locked.status
    restored_status = activation.old_status
    enrollment = _restore_activation_enrollment(
        metadata,
        activation_created_at=activation.created_at,
    )

    locked.status = restored_status
    locked.save(update_fields=["status", "updated_at"])
    _record_decision(
        locked,
        DECISION_ACTION_PAYMENT_WAIVER_REVOKED,
        old_status,
        restored_status,
        actor,
        reason=reason,
        metadata={
            "payment_waiver_id": str(waiver_id),
            "enrollment_id": metadata.get("enrollment_id"),
            "enrollment_type": metadata.get("enrollment_type"),
            "enrollment_reset": enrollment is not None,
        },
    )
    record_audit_event(
        actor=actor,
        action=ACTION_APPLICATION_STATUS_CHANGE,
        target_type="program_application",
        target_id=str(locked.pk),
        metadata={
            "old_status": old_status,
            "new_status": restored_status,
            "reason": reason,
            "payment_waiver_id": str(waiver_id),
            "enrollment_id": metadata.get("enrollment_id"),
            "enrollment_reset": enrollment is not None,
        },
    )
    return {
        "rolled_back": True,
        "application_status_before": old_status,
        "application_status_after": restored_status,
        "enrollment_reset": enrollment is not None,
    }


@transaction.atomic
def rollback_payment_activation(application_id, payment_record_id, actor=None, reason: str = "") -> dict:
    """Rollback only an activation proven to depend on one payment record."""
    locked = ProgramApplication.objects.select_for_update().get(pk=application_id)
    result = {
        "rolled_back": False,
        "application_status_before": locked.status,
        "application_status_after": locked.status,
        "enrollment_reset": False,
    }
    if locked.status != PROGRAM_APP_STATUS_ACTIVATED:
        return result
    if has_active_application_waiver(locked) or has_application_paid_record(locked):
        return result

    activation = locked.decisions.filter(
        action=DECISION_ACTION_ACTIVATE,
    ).order_by("-created_at", "-id").first()
    metadata = activation.metadata if activation else {}
    if not activation or str(metadata.get("payment_record_id") or "") != str(payment_record_id):
        return result
    if metadata.get("enrollment_changed_by_activation") is False:
        return result

    enrollment = _restore_activation_enrollment(
        metadata,
        activation_created_at=activation.created_at,
    )

    old_status = locked.status
    restored_status = activation.old_status
    locked.status = restored_status
    locked.save(update_fields=["status", "updated_at"])
    record_program_application_note(
        locked,
        actor,
        note=_("Payment correction rolled back access created by this payment."),
        metadata={
            "payment_record_id": str(payment_record_id),
            "enrollment_id": metadata.get("enrollment_id"),
            "enrollment_type": metadata.get("enrollment_type"),
            "enrollment_reset": enrollment is not None,
            "reason": reason,
        },
    )
    record_audit_event(
        actor=actor,
        action=ACTION_APPLICATION_STATUS_CHANGE,
        target_type="program_application",
        target_id=str(locked.pk),
        metadata={
            "old_status": old_status,
            "new_status": restored_status,
            "payment_record_id": str(payment_record_id),
            "enrollment_id": metadata.get("enrollment_id"),
            "enrollment_reset": enrollment is not None,
            "reason": reason,
        },
    )
    return {
        "rolled_back": True,
        "application_status_before": old_status,
        "application_status_after": restored_status,
        "enrollment_reset": enrollment is not None,
    }


def _find_existing_enrollment(application):
    """Find the enrollment previously created for this activated application."""
    if application.program_type == PROGRAM_TYPE_COURSE and application.course:
        return CourseEnrollment.objects.filter(
            student=application.student,
            course_version__course=application.course,
        ).first()
    if application.program_type == PROGRAM_TYPE_DIPLOMA and application.cohort:
        return DiplomaEnrollment.objects.filter(
            student=application.student,
            cohort=application.cohort,
        ).first()
    return None


def _find_paid_record_id(application):
    """Return the id of the paid PaymentRecord for this application, or None."""
    record = PaymentRecord.objects.filter(
        application=application,
        status=PAYMENT_STATUS_PAID,
        deleted_at__isnull=True,
    ).first()
    return record.pk if record else None


LEGAL_PAYMENT_STATUSES = {
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_PAYMENT_PENDING,
    PROGRAM_APP_STATUS_DOCUMENTS_REQUESTED,
    PROGRAM_APP_STATUS_DOCUMENTS_SUBMITTED,
    PROGRAM_APP_STATUS_SURVEY_REQUESTED,
    PROGRAM_APP_STATUS_SURVEY_SUBMITTED,
    PROGRAM_APP_STATUS_READY_FOR_ACTIVATION,
}


# ---------------------------------------------------------------------------
# Canonical available-action authority (BUG-006)
# ---------------------------------------------------------------------------
#
# UI action visibility for a ProgramApplication is derived from this single
# source so the buttons shown always match the exact read gates used by the
# mutation services. Each status set mirrors the gate of the corresponding
# transition function, while the Exists annotations below make the same gates
# available before queue pagination:
#
#   accept              -> accept_program_application
#   reject              -> reject_program_application
#   mark_paid           -> mark_application_paid (LEGAL_PAYMENT_STATUSES)
#   request_intake      -> request_intake_form_for_application (INTAKE_REQUESTABLE_STATUSES)
#   request_documents   -> request_documents_program_application
#   request_interview   -> request_interview_program_application
#   invite_to_entrance  -> invite_to_entrance_exam
#   grant_waiver        -> grant_payment_waiver (payments WAIVER_LEGAL_APPLICATION_STATUSES)
#   activate            -> activate_application_if_ready (readiness via can_activate_application)
#
# The mutation services still re-check every transition under a row lock.

ACTION_ACCEPT = "accept"
ACTION_REJECT = "reject"
ACTION_MARK_PAID = "mark_paid"
ACTION_REQUEST_INTAKE = "request_intake"
ACTION_REQUEST_DOCUMENTS = "request_documents"
ACTION_REQUEST_INTERVIEW = "request_interview"
ACTION_INVITE_TO_ENTRANCE = "invite_to_entrance"
ACTION_GRANT_WAIVER = "grant_waiver"
ACTION_ACTIVATE = "activate"
ACTION_REVERT = "revert"

APPLICATION_ACTION_LABELS = {
    ACTION_ACCEPT: _("Accept application"),
    ACTION_REJECT: _("Reject application"),
    ACTION_MARK_PAID: _("Record payment"),
    ACTION_REQUEST_INTAKE: _("Request survey"),
    ACTION_REQUEST_DOCUMENTS: _("Request documents"),
    ACTION_REQUEST_INTERVIEW: _("Request interview"),
    ACTION_INVITE_TO_ENTRANCE: _("Assign entrance assessment"),
    ACTION_GRANT_WAIVER: _("Grant payment waiver"),
    ACTION_ACTIVATE: _("Activate application"),
    ACTION_REVERT: _("Revert decision"),
}

APPLICATION_ACTION_CATEGORIES = {
    ACTION_ACCEPT: "review",
    ACTION_REJECT: "review",
    ACTION_MARK_PAID: "payment",
    ACTION_REQUEST_INTAKE: "survey",
    ACTION_REQUEST_DOCUMENTS: "documents",
    ACTION_REQUEST_INTERVIEW: "review",
    ACTION_INVITE_TO_ENTRANCE: "entrance",
    ACTION_GRANT_WAIVER: "payment",
    ACTION_ACTIVATE: "activation",
    ACTION_REVERT: "review",
}

APPLICATION_ACTION_PRIORITY = {
    ACTION_ACCEPT: 10,
    ACTION_REJECT: 10,
    ACTION_INVITE_TO_ENTRANCE: 20,
    ACTION_REQUEST_INTERVIEW: 30,
    ACTION_REQUEST_DOCUMENTS: 40,
    ACTION_REQUEST_INTAKE: 50,
    ACTION_MARK_PAID: 60,
    ACTION_GRANT_WAIVER: 65,
    ACTION_ACTIVATE: 70,
    ACTION_REVERT: 100,
}

APPLICATION_QUEUE_STATUS_PRIORITY = {
    PROGRAM_APP_STATUS_SUBMITTED: 10,
    PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED: 15,
    PROGRAM_APP_STATUS_INVITED: 20,
    PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED: 30,
    PROGRAM_APP_STATUS_INTERVIEW_REQUESTED: 35,
    PROGRAM_APP_STATUS_ACCEPTED: 40,
    PROGRAM_APP_STATUS_DOCUMENTS_REQUESTED: 50,
    PROGRAM_APP_STATUS_DOCUMENTS_SUBMITTED: 60,
    PROGRAM_APP_STATUS_SURVEY_REQUESTED: 70,
    PROGRAM_APP_STATUS_SURVEY_SUBMITTED: 80,
    PROGRAM_APP_STATUS_PAYMENT_PENDING: 90,
    PROGRAM_APP_STATUS_READY_FOR_ACTIVATION: 100,
    PROGRAM_APP_STATUS_REJECTED: 900,
    PROGRAM_APP_STATUS_ACTIVATED: 950,
}

APPLICATION_QUEUE_ACTIONS = frozenset(
    {
        ACTION_ACCEPT,
        ACTION_REJECT,
        ACTION_MARK_PAID,
        ACTION_REQUEST_INTAKE,
        ACTION_REQUEST_DOCUMENTS,
        ACTION_REQUEST_INTERVIEW,
        ACTION_INVITE_TO_ENTRANCE,
        ACTION_GRANT_WAIVER,
        ACTION_ACTIVATE,
    }
)

_ACCEPT_ALLOWED_STATUSES = {
    PROGRAM_TYPE_COURSE: frozenset({
        PROGRAM_APP_STATUS_SUBMITTED,
        PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED,
        PROGRAM_APP_STATUS_PAYMENT_PENDING,
    }),
    PROGRAM_TYPE_DIPLOMA: frozenset({
        PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED,
        PROGRAM_APP_STATUS_INTERVIEW_REQUESTED,
    }),
}

_REJECT_ALLOWED_STATUSES = {
    PROGRAM_TYPE_COURSE: frozenset({
        PROGRAM_APP_STATUS_SUBMITTED,
        PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED,
        PROGRAM_APP_STATUS_PAYMENT_PENDING,
        PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED,
        PROGRAM_APP_STATUS_INTERVIEW_REQUESTED,
    }),
    PROGRAM_TYPE_DIPLOMA: frozenset({
        PROGRAM_APP_STATUS_INVITED,
        PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED,
        PROGRAM_APP_STATUS_INTERVIEW_REQUESTED,
    }),
}

_REQUEST_DOCUMENTS_ALLOWED_STATUSES = frozenset({
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_DOCUMENTS_REQUESTED,
})

_INVITE_TO_ENTRANCE_ALLOWED_STATUSES = frozenset({
    PROGRAM_APP_STATUS_SUBMITTED,
    PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED,
})


def _waiver_legal_application_statuses() -> frozenset:
    """Waiver-eligible statuses, taken from the payment mutation authority.

    Imported lazily to avoid a module-load import cycle (payments.services
    imports from apps.applications.services).
    """
    return frozenset(payment_services.WAIVER_LEGAL_APPLICATION_STATUSES)


def _has_required_intake_form(application) -> bool:
    """Return whether an unresolved required form can currently be requested."""
    cached = getattr(application, "_has_required_intake_form", None)
    if cached is not None:
        return bool(cached)
    required_forms = get_required_intake_forms_for_application(application)
    response_form_ids = set(
        IntakeFormResponse.objects.filter(
            program_application=application,
            form_id__in=[form.pk for form in required_forms],
        ).values_list("form_id", flat=True)
    )
    return any(form.is_open() and form.pk not in response_form_ids for form in required_forms)


def _has_entrance_attempt(application) -> bool:
    cached = getattr(application, "_has_entrance_attempt", None)
    if cached is not None:
        return bool(cached)
    return application.entrance_attempts.exists()


def _application_action_predicates() -> dict[str, Q]:
    """Return SQL predicates for the exact application action gates.

    The aliases referenced here are installed by
    ``annotate_application_action_readiness``. They intentionally mirror the
    read helpers consumed by ``allowed_application_actions`` so list filters,
    dashboard counts, queue ordering, and detail presentation share one gate.
    """
    status = Q
    documents_ready = (
        ~status(program_type=PROGRAM_TYPE_DIPLOMA)
        | status(_has_submitted_documents=True)
    )
    intake_ready = status(_has_required_intake_response=True)
    payment_ready = status(_has_paid_record=True) | status(_has_active_waiver=True)
    account_ready = status(student__user__email_activated_at__isnull=False)

    accept = Q()
    reject = Q()
    for program_type, statuses in _ACCEPT_ALLOWED_STATUSES.items():
        accept |= status(program_type=program_type, status__in=statuses)
    for program_type, statuses in _REJECT_ALLOWED_STATUSES.items():
        reject |= status(program_type=program_type, status__in=statuses)

    course_activation = status(
        program_type=PROGRAM_TYPE_COURSE,
        status__in=(PROGRAM_APP_STATUS_ACCEPTED, PROGRAM_APP_STATUS_PAYMENT_PENDING),
        course_id__isnull=False,
        _has_active_course_version=True,
    )
    diploma_activation = status(
        program_type=PROGRAM_TYPE_DIPLOMA,
        status__in=(
            PROGRAM_APP_STATUS_DOCUMENTS_SUBMITTED,
            PROGRAM_APP_STATUS_PAYMENT_PENDING,
            PROGRAM_APP_STATUS_READY_FOR_ACTIVATION,
        ),
        cohort_id__isnull=False,
    ) & status(_has_submitted_documents=True)

    return {
        ACTION_ACCEPT: accept,
        ACTION_REJECT: reject,
        ACTION_MARK_PAID: (
            status(status__in=LEGAL_PAYMENT_STATUSES)
            & ~status(_has_paid_record=True)
            & ~status(_has_active_waiver=True)
            & documents_ready
            & intake_ready
        ),
        ACTION_REQUEST_INTAKE: (
            status(status__in=INTAKE_REQUESTABLE_STATUSES)
            & status(_has_required_intake_form=True)
            & ~status(_has_required_intake_response=True)
            & documents_ready
        ),
        ACTION_REQUEST_DOCUMENTS: status(
            program_type=PROGRAM_TYPE_DIPLOMA,
            status__in=_REQUEST_DOCUMENTS_ALLOWED_STATUSES,
            cohort_id__isnull=False,
            student__user__email_activated_at__isnull=False,
        ),
        ACTION_REQUEST_INTERVIEW: status(
            program_type=PROGRAM_TYPE_DIPLOMA,
            status=PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED,
        ),
        ACTION_INVITE_TO_ENTRANCE: status(
            program_type=PROGRAM_TYPE_DIPLOMA,
            status__in=_INVITE_TO_ENTRANCE_ALLOWED_STATUSES,
        ),
        ACTION_GRANT_WAIVER: (
            status(status__in=_waiver_legal_application_statuses())
            & ~status(_has_paid_record=True)
            & ~status(_has_active_waiver=True)
            & documents_ready
            & intake_ready
        ),
        ACTION_ACTIVATE: (
            account_ready
            & payment_ready
            & intake_ready
            & (course_activation | diploma_activation)
        ),
        ACTION_REVERT: (
            status(status__in=(PROGRAM_APP_STATUS_ACCEPTED, PROGRAM_APP_STATUS_REJECTED))
            & ~status(_has_entrance_attempt=True)
        ),
    }


def annotate_application_action_readiness(queryset):
    """Annotate a queryset with the set-based readiness used by the queue.

    The annotations mirror the instance read helpers below. They let the
    queue order and filter before pagination without invoking one read query
    per row in the presenter.
    """
    now = timezone.now()
    latest_course_version = CourseVersion.objects.filter(
        course_id=OuterRef(OuterRef("course_id")),
        is_active=True,
    ).order_by(*ACTIVE_COURSE_VERSION_ORDERING)
    course_required_forms = IntakeForm.objects.filter(
        is_active=True,
        is_required=True,
        course_version_id=Subquery(latest_course_version.values("pk")[:1]),
    )
    course_open_forms = course_required_forms.filter(
        Q(open_at__isnull=True) | Q(open_at__lte=now),
        Q(close_at__isnull=True) | Q(close_at__gt=now),
    ).order_by(*REQUIRED_INTAKE_FORM_ORDERING)
    diploma_required_forms = IntakeForm.objects.filter(
        is_active=True,
        is_required=True,
        cohort_id=OuterRef("cohort_id"),
    )
    diploma_open_forms = diploma_required_forms.filter(
        Q(open_at__isnull=True) | Q(open_at__lte=now),
        Q(close_at__isnull=True) | Q(close_at__gt=now),
    ).order_by(*REQUIRED_INTAKE_FORM_ORDERING)

    course_missing_response_forms = course_required_forms.exclude(
        responses__program_application_id=OuterRef("pk"),
    )
    diploma_missing_response_forms = diploma_required_forms.exclude(
        responses__program_application_id=OuterRef("pk"),
    )
    course_open_missing_response_forms = course_open_forms.exclude(
        responses__program_application_id=OuterRef("pk"),
    )
    diploma_open_missing_response_forms = diploma_open_forms.exclude(
        responses__program_application_id=OuterRef("pk"),
    )

    annotated = queryset.annotate(
        _has_paid_record=Exists(
            PaymentRecord.objects.filter(
                application_id=OuterRef("pk"),
                status=PAYMENT_STATUS_PAID,
                deleted_at__isnull=True,
            )
        ),
        _has_active_waiver=Exists(
            PaymentWaiver.objects.filter(
                application_id=OuterRef("pk"),
                status=WAIVER_STATUS_ACTIVE,
            )
        ),
        _has_submitted_documents=Exists(
            ApplicationDocumentUpload.objects.filter(
                request__program_application_id=OuterRef("pk"),
            )
        ),
        _has_course_required_intake_form=Exists(course_open_missing_response_forms),
        _has_diploma_required_intake_form=Exists(diploma_open_missing_response_forms),
        _has_course_required_intake_requirement=Exists(course_required_forms),
        _has_diploma_required_intake_requirement=Exists(diploma_required_forms),
        _has_course_missing_intake_response=Exists(course_missing_response_forms),
        _has_diploma_missing_intake_response=Exists(diploma_missing_response_forms),
        _has_active_course_version=Exists(
            CourseVersion.objects.filter(
                course_id=OuterRef("course_id"),
                is_active=True,
            )
        ),
        _has_entrance_attempt=Exists(
            EntranceAttempt.objects.filter(program_application_id=OuterRef("pk"))
        ),
    ).annotate(
        _has_required_intake_form=Case(
            When(
                program_type=PROGRAM_TYPE_COURSE,
                _has_course_required_intake_form=True,
                then=Value(True),
            ),
            When(
                program_type=PROGRAM_TYPE_DIPLOMA,
                _has_diploma_required_intake_form=True,
                then=Value(True),
            ),
            default=Value(False),
            output_field=BooleanField(),
        ),
        _has_required_intake_response=Case(
            When(
                program_type=PROGRAM_TYPE_COURSE,
                _has_course_required_intake_requirement=False,
                then=Value(True),
            ),
            When(
                program_type=PROGRAM_TYPE_COURSE,
                _has_course_missing_intake_response=False,
                then=Value(True),
            ),
            When(
                program_type=PROGRAM_TYPE_DIPLOMA,
                _has_diploma_required_intake_requirement=False,
                then=Value(True),
            ),
            When(
                program_type=PROGRAM_TYPE_DIPLOMA,
                _has_diploma_missing_intake_response=False,
                then=Value(True),
            ),
            default=Value(False),
            output_field=BooleanField(),
        ),
    )

    predicates = _application_action_predicates()
    status_priority = Case(
        *[
            When(status=status, then=Value(priority))
            for status, priority in APPLICATION_QUEUE_STATUS_PRIORITY.items()
        ],
        default=Value(999),
        output_field=IntegerField(),
    )
    return annotated.annotate(
        _queue_priority=Case(
            *[
                When(predicates[action], then=Value(APPLICATION_ACTION_PRIORITY[action]))
                for action in sorted(
                    APPLICATION_QUEUE_ACTIONS,
                    key=lambda key: (APPLICATION_ACTION_PRIORITY[key], key),
                )
            ],
            default=status_priority,
            output_field=IntegerField(),
        )
    )


def allowed_application_actions(application) -> frozenset[str]:
    """Return only actions whose mutation-service gates currently pass."""
    status = application.status
    program_type = application.program_type
    allowed: set[str] = set()
    waiver_statuses = _waiver_legal_application_statuses()
    has_paid = has_application_paid_record(application)
    has_waiver = has_active_application_waiver(application)
    has_documents = has_application_documents_submitted(application)
    has_required_form = _has_required_intake_form(application)
    has_required_response = _has_required_intake_check(application)
    documents_gate_satisfied = (
        program_type != PROGRAM_TYPE_DIPLOMA or has_documents
    )
    intake_gate_satisfied = has_required_response

    if status in _ACCEPT_ALLOWED_STATUSES.get(program_type, frozenset()):
        allowed.add(ACTION_ACCEPT)
    if status in _REJECT_ALLOWED_STATUSES.get(program_type, frozenset()):
        allowed.add(ACTION_REJECT)
    if (
        status in LEGAL_PAYMENT_STATUSES
        and not has_paid
        and not has_waiver
        and documents_gate_satisfied
        and intake_gate_satisfied
    ):
        allowed.add(ACTION_MARK_PAID)
    if (
        status in INTAKE_REQUESTABLE_STATUSES
        and has_required_form
        and not has_required_response
        and documents_gate_satisfied
    ):
        allowed.add(ACTION_REQUEST_INTAKE)
    if (
        program_type == PROGRAM_TYPE_DIPLOMA
        and status in _REQUEST_DOCUMENTS_ALLOWED_STATUSES
        and application.cohort_id
        and application.student.user.email_activated_at
    ):
        allowed.add(ACTION_REQUEST_DOCUMENTS)
    if program_type == PROGRAM_TYPE_DIPLOMA and status == PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED:
        allowed.add(ACTION_REQUEST_INTERVIEW)
    if program_type == PROGRAM_TYPE_DIPLOMA and status in _INVITE_TO_ENTRANCE_ALLOWED_STATUSES:
        allowed.add(ACTION_INVITE_TO_ENTRANCE)
    if (
        status in waiver_statuses
        and not has_paid
        and not has_waiver
        and documents_gate_satisfied
        and intake_gate_satisfied
    ):
        allowed.add(ACTION_GRANT_WAIVER)
    if can_activate_application(application).ready:
        allowed.add(ACTION_ACTIVATE)
    if (
        status in {PROGRAM_APP_STATUS_ACCEPTED, PROGRAM_APP_STATUS_REJECTED}
        and not _has_entrance_attempt(application)
    ):
        allowed.add(ACTION_REVERT)

    return frozenset(allowed)


def get_application_action_projection(application) -> dict:
    """Return the canonical translated action/readiness projection.

    This remains application-specific: it exposes the actions that the
    application state machine and its document/intake/payment gates currently
    permit. Templates consume the projection but never recreate these rules.
    """
    allowed = allowed_application_actions(application)
    actions = []
    for action in sorted(
        allowed,
        key=lambda key: (APPLICATION_ACTION_PRIORITY.get(key, 999), key),
    ):
        actions.append(
            {
                "key": action,
                "label": APPLICATION_ACTION_LABELS.get(action, _("Available action")),
                "category": APPLICATION_ACTION_CATEGORIES.get(action, "workflow"),
                "method": "POST",
                "queue_relevant": action in APPLICATION_QUEUE_ACTIONS,
            }
        )
    queue_actions = [action for action in actions if action["queue_relevant"]]
    next_action = queue_actions[0] if queue_actions else None
    return {
        "state": application.status,
        "state_label": application.get_status_display(),
        "available_actions": actions,
        "available_action_keys": tuple(action["key"] for action in actions),
        "action_needed": bool(queue_actions),
        "next_action": next_action,
        "next_action_label": next_action["label"] if next_action else None,
        "next_action_category": next_action["category"] if next_action else None,
        "queue_priority": (
            APPLICATION_ACTION_PRIORITY[next_action["key"]]
            if next_action
            else APPLICATION_QUEUE_STATUS_PRIORITY.get(application.status, 999)
        ),
        "terminal": application.status in {
            PROGRAM_APP_STATUS_REJECTED,
            PROGRAM_APP_STATUS_ACTIVATED,
        },
    }


def filter_applications_for_action(queryset, action_filter: str):
    """Filter with the same set-based action predicates as the projection."""
    if "_queue_priority" not in queryset.query.annotations:
        queryset = annotate_application_action_readiness(queryset)
    if action_filter in {"", "all"}:
        return queryset
    predicates = _application_action_predicates()
    if action_filter == "action":
        actions = APPLICATION_QUEUE_ACTIONS
    elif action_filter == "review":
        actions = {
            ACTION_ACCEPT,
            ACTION_REJECT,
            ACTION_REQUEST_INTERVIEW,
            ACTION_INVITE_TO_ENTRANCE,
        }
    elif action_filter == "payment":
        actions = {ACTION_MARK_PAID, ACTION_GRANT_WAIVER}
    elif action_filter == "documents":
        actions = {ACTION_REQUEST_DOCUMENTS}
    elif action_filter == "survey":
        actions = {ACTION_REQUEST_INTAKE}
    elif action_filter == "entrance":
        actions = {ACTION_INVITE_TO_ENTRANCE}
    elif action_filter == "activation":
        actions = {ACTION_ACTIVATE}
    else:
        return queryset.none()
    combined = Q()
    for action in actions:
        combined |= predicates[action]
    return queryset.filter(combined)


def get_application_portal_projection(application, *, now=None) -> dict:
    """Project one application for the student dashboard and portal.

    This application-owned projection contains the only student application
    action list. It exposes real document, survey, and entrance destinations;
    payment and activation remain state/next-step text because the student
    has no same-page mutation or read-only destination to invoke there.
    """
    now = now or timezone.now()
    is_paid = has_application_paid_record(application)
    is_waived = has_active_application_waiver(application)
    if is_paid:
        payment_state, payment_state_label = "paid", _("Payment confirmed")
    elif is_waived:
        payment_state, payment_state_label = "waived", _("Payment waived")
    else:
        payment_state, payment_state_label = "pending", _("Payment pending")

    document_requests = list(application.document_requests.all())
    entrance_attempts = list(application.entrance_attempts.all())

    submitted_documents = False
    for request in document_requests:
        if request.status != DOCUMENT_REQUEST_STATUS_SUBMITTED:
            continue
        try:
            if request.upload is not None:
                submitted_documents = True
                break
        except ApplicationDocumentUpload.DoesNotExist:
            continue
    open_document_request = next(
        (
            request for request in document_requests
            if request.status == DOCUMENT_REQUEST_STATUS_REQUESTED
            and request.expires_at > now
        ),
        None,
    )
    if submitted_documents:
        documents_state, documents_state_label = "submitted", _("Documents submitted")
    elif open_document_request:
        documents_state, documents_state_label = "requested", _("Documents requested")
    elif any(request.status == DOCUMENT_REQUEST_STATUS_EXPIRED for request in document_requests):
        documents_state, documents_state_label = "expired", _("Documents request expired")
    elif any(request.status == DOCUMENT_REQUEST_STATUS_CANCELLED for request in document_requests):
        documents_state, documents_state_label = "cancelled", _("Documents request cancelled")
    else:
        documents_state, documents_state_label = "not_requested", _("Documents not requested")

    intake_requirement_states = get_intake_requirement_states(application, now=now)
    pending_intake_states = [
        state for state in intake_requirement_states if not state["has_response"]
    ]
    open_intake_states = [
        state for state in pending_intake_states if state["open_request"] is not None
    ]
    has_intake_response = bool(intake_requirement_states) and not pending_intake_states
    if not intake_requirement_states or has_intake_response:
        intake_state, intake_state_label = "submitted", _("Survey submitted")
    elif open_intake_states:
        intake_state, intake_state_label = "requested", _("Survey requested")
    elif any(state["state"] == "expired" for state in pending_intake_states):
        intake_state, intake_state_label = "expired", _("Required survey expired")
    elif any(state["state"] == "closed" for state in pending_intake_states):
        intake_state, intake_state_label = "closed", _("Required survey is closed")
    elif application.status == PROGRAM_APP_STATUS_SURVEY_REQUESTED:
        intake_state, intake_state_label = "requested", _("Survey requested")
    else:
        intake_state, intake_state_label = "available", _("Required survey is ready")

    actions = []
    next_step = None
    if application.status == PROGRAM_APP_STATUS_INVITED:
        attempt = entrance_attempts[0] if entrance_attempts else None
        exam = attempt.exam if attempt else None
        if attempt and attempt.submitted_at:
            next_step = _(
                "Your entrance exam has been submitted. Awaiting the admissions office decision."
            )
        elif exam and exam.is_active and exam.access_token:
            if (not exam.open_at or exam.open_at <= now) and (
                not exam.close_at or exam.close_at > now
            ):
                actions.append({
                    "kind": "entrance",
                    "title": exam.title,
                    "description": _("Your entrance exam is ready."),
                    "label": _("Open exam"),
                    "url": reverse("entrance_exams:exam_access_token", args=[exam.access_token]),
                    "method": "GET",
                    "priority": "urgent",
                    "due_at": exam.close_at if exam.close_at and exam.close_at > now else None,
                })
            else:
                next_step = _(
                    "Your entrance assessment is scheduled. Use the link you received by email when it opens."
                )
        else:
            next_step = _("Your entrance assessment has not been assigned yet.")
    elif application.status == PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED:
        next_step = _(
            "Your entrance exam has been submitted. Awaiting the admissions office decision."
        )

    if open_document_request:
        actions.append({
            "kind": "document",
            "title": _("Upload requested documents"),
            "description": _("Waiting for your documents — open the upload from your applications."),
            "label": _("Upload documents"),
            "url": reverse("applications:my_document_upload", args=[application.id]),
            "method": "GET",
            "priority": "urgent" if (open_document_request.expires_at - now).days < 7 else "normal",
            "due_at": open_document_request.expires_at,
        })
    for intake_state_item in open_intake_states:
        open_intake_request = intake_state_item["open_request"]
        actions.append({
            "kind": "intake",
            "title": intake_state_item["form"].title,
            "description": _("A required survey is waiting for your response."),
            "label": _("Open survey"),
            "url": reverse("intake_forms:student_intake_form", args=[open_intake_request.pk]),
            "method": "GET",
            "priority": "urgent" if (open_intake_request.expires_at - now).days < 7 else "normal",
            "due_at": open_intake_request.expires_at,
        })

    if application.status == PROGRAM_APP_STATUS_PAYMENT_PENDING:
        if is_waived:
            next_step = _("Payment waived by the admissions office.")
        elif is_paid:
            next_step = _("Payment confirmed; awaiting activation.")
        else:
            next_step = _("Awaiting payment confirmation by the admissions office.")
    elif application.status in {
        PROGRAM_APP_STATUS_READY_FOR_ACTIVATION,
        PROGRAM_APP_STATUS_DOCUMENTS_SUBMITTED,
        PROGRAM_APP_STATUS_SURVEY_SUBMITTED,
    } and (is_paid or is_waived):
        next_step = _("Your application is ready for activation by the admissions office.")

    if (
        pending_intake_states
        and not open_intake_states
        and any(state["state"] in {"closed", "expired"} for state in pending_intake_states)
    ):
        next_step = _(
            "A required survey needs admissions attention before payment or activation can continue."
        )

    if len(actions) > 1:
        next_step = _("Complete the available application actions.")

    return {
        "payment_state": payment_state,
        "payment_state_label": payment_state_label,
        "documents_state": documents_state,
        "documents_state_label": documents_state_label,
        "documents_count": sum(
            request.status == DOCUMENT_REQUEST_STATUS_SUBMITTED for request in document_requests
        ),
        "intake_state": intake_state,
        "intake_state_label": intake_state_label,
        "activation_state": "activated" if application.status == PROGRAM_APP_STATUS_ACTIVATED else "pending",
        "activation_state_label": (
            _("Application activated")
            if application.status == PROGRAM_APP_STATUS_ACTIVATED
            else _("Awaiting activation")
        ),
        "available_actions": actions,
        "next_step": next_step,
    }


def _has_application_action(application, action: str) -> bool:
    return action in allowed_application_actions(application)


def can_accept_application(application) -> bool:
    """Whether ``accept_program_application`` may run for this application."""
    return _has_application_action(application, ACTION_ACCEPT)


def can_reject_application(application) -> bool:
    """Whether ``reject_program_application`` may run for this application."""
    return _has_application_action(application, ACTION_REJECT)


def can_mark_paid_application(application) -> bool:
    """Whether ``mark_application_paid`` may run for this application."""
    return _has_application_action(application, ACTION_MARK_PAID)


def can_request_intake_application(application) -> bool:
    """Whether ``request_intake_form_for_application`` may run (status gate)."""
    return _has_application_action(application, ACTION_REQUEST_INTAKE)


def can_request_documents_application(application) -> bool:
    """Whether ``request_documents_program_application`` may run (status gate)."""
    return _has_application_action(application, ACTION_REQUEST_DOCUMENTS)


def can_request_interview_application(application) -> bool:
    """Whether ``request_interview_program_application`` may run (status gate)."""
    return _has_application_action(application, ACTION_REQUEST_INTERVIEW)


def can_invite_to_entrance_application(application) -> bool:
    """Whether ``invite_to_entrance_exam`` may run for this application."""
    return _has_application_action(application, ACTION_INVITE_TO_ENTRANCE)


def can_grant_waiver_application(application) -> bool:
    """Whether ``grant_payment_waiver`` may run for this application."""
    return _has_application_action(application, ACTION_GRANT_WAIVER)


@transaction.atomic
def mark_application_paid(
    application,
    actor,
    *,
    amount=0,
    paid_at=None,
    external_reference="",
    notes="",
) -> object:
    """Reconcile an application payment through the canonical payment path."""
    record = payment_services.reconcile_payment_record(
        actor=actor,
        application=application,
        amount=amount,
        status=PAYMENT_STATUS_PAID,
        paid_at=paid_at,
        external_reference=external_reference,
        notes=notes,
        target_type=application.program_type,
        target_snapshot=_build_application_snapshot(application),
        source="application",
        attempt_activation=True,
    )
    record_audit_event(
        actor=actor,
        action="application.paid",
        target_type="program_application",
        target_id=str(application.pk),
        metadata={"amount": str(record.amount), "external_reference": external_reference, "payment_record_id": str(record.pk)},
    )
    return record


def _build_application_snapshot(application) -> dict:
    snapshot = {}
    if application.cohort:
        snapshot["cohort_name"] = application.cohort.name or ""
        if application.cohort.diploma:
            snapshot["diploma_title"] = application.cohort.diploma.title or ""
    if application.course:
        snapshot["course_title"] = application.course.title or ""
    return snapshot


# ---------------------------------------------------------------------------
# Email helpers
# ---------------------------------------------------------------------------


def _send_program_app_acceptance_email(prog_app, actor=None) -> None:
    email = prog_app.student.user.email
    if not email:
        return
    context = {
        "full_name": prog_app.student.full_name or "",
        "cohort_name": prog_app.cohort.name if prog_app.cohort else "",
        "diploma_title": prog_app.cohort.diploma.title if prog_app.cohort and prog_app.cohort.diploma else "",
        "program_type": prog_app.program_type or "",
    }
    try:
        queue_program_application_email(
            program_application=prog_app,
            action=EMAIL_ACTION_ACCEPTANCE,
            template_slug=TEMPLATE_ACCEPTANCE,
            to_email=email,
            context=context,
            sent_by=actor,
            idempotency_key=f"application:acceptance:app:{prog_app.pk}",
            reference=f"app:{prog_app.pk}",
        )
    except Exception:
        logger.exception("Acceptance email queue failed for application %s", prog_app.pk)


def _send_program_app_rejection_email(prog_app, reason: str, actor=None) -> None:
    email = prog_app.student.user.email
    if not email:
        return
    context = {
        "full_name": prog_app.student.full_name or "",
        "rejection_reason": reason,
        "cohort_name": prog_app.cohort.name if prog_app.cohort else "",
    }
    try:
        queue_program_application_email(
            program_application=prog_app,
            action=EMAIL_ACTION_REJECTION,
            template_slug=TEMPLATE_REJECTION,
            to_email=email,
            context=context,
            sent_by=actor,
            idempotency_key=f"application:rejection:app:{prog_app.pk}",
            reference=f"app:{prog_app.pk}",
        )
    except Exception:
        logger.exception("Rejection email queue failed for application %s", prog_app.pk)
