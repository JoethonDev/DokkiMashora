"""Application business logic: duplicate checking, submission, and state machine."""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.academics.constants import ENROLLMENT_STATUS_PENDING
from apps.academics.models import Cohort, Course, CourseEnrollment, DiplomaEnrollment
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
from apps.core.permissions import validate_score
from apps.core.phone import normalize_phone
from apps.intake_forms import services as intake_services
from apps.intake_forms.constants import INTAKE_REQUESTABLE_STATUSES  # noqa: E402
from apps.intake_forms.selectors import has_required_intake_response  # noqa: E402
from apps.payments import services as payment_services
from apps.payments.constants import CURRENCY_EGP, PAYMENT_STATUS_PAID, WAIVER_STATUS_ACTIVE
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
    if application.status != PROGRAM_APP_STATUS_SUBMITTED:
        raise ValueError(_("Only submitted applications can be marked account_activated."))
    old_status = application.status
    _set_status(application, PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED)
    _record_decision(application, DECISION_ACTION_ACCOUNT_ACTIVATED, old_status, PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED, actor)
    record_audit_event(
        actor=actor,
        action="application.account_activated",
        target_type="program_application",
        target_id=str(application.pk),
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
    allowed = [PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED, PROGRAM_APP_STATUS_ACCEPTED, PROGRAM_APP_STATUS_PAYMENT_PENDING, PROGRAM_APP_STATUS_INVITED]
    allowed.append(PROGRAM_APP_STATUS_SURVEY_SUBMITTED)
    if application.status not in allowed:
        raise ValueError(_("Application must be in account_activated status to send post-activation email."))

    form = intake_services.get_required_intake_form_for_application(application)
    if form and not has_required_intake_response(application):
        intake_services.ensure_required_intake_request(application, actor=actor)
        return

    if application.program_type == PROGRAM_TYPE_COURSE:
        email_action = "course_payment_instructions"
        if not force_resend and _has_non_resend_email_log(application, email_action):
            return
        transaction.on_commit(lambda: _send_course_payment_instructions_email(application, actor))
        _advance_to(application, PROGRAM_APP_STATUS_PAYMENT_PENDING, DECISION_ACTION_PAYMENT_PENDING, actor)

    elif application.program_type == PROGRAM_TYPE_DIPLOMA:
        if application.status == PROGRAM_APP_STATUS_SURVEY_SUBMITTED:
            _advance_to(application, PROGRAM_APP_STATUS_PAYMENT_PENDING, DECISION_ACTION_PAYMENT_PENDING, actor)
            return
        email_action = "diploma_entrance_exam_schedule"
        if not force_resend and _has_non_resend_email_log(application, email_action):
            return
        transaction.on_commit(lambda: _send_diploma_entrance_schedule_email(application, actor))
        _advance_to(application, PROGRAM_APP_STATUS_INVITED, DECISION_ACTION_INVITED_TO_ENTRANCE, actor)


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
    if application.status not in (PROGRAM_APP_STATUS_INVITED, PROGRAM_APP_STATUS_SUBMITTED):
        raise ValueError(_("Only invited applications can submit entrance exam."))
    old_status = application.status
    _set_status(application, PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED)
    metadata = {}
    if attempt:
        metadata["attempt_id"] = attempt.pk
    _record_decision(
        application, DECISION_ACTION_ENTRANCE_SUBMITTED,
        old_status, PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED, actor,
        metadata=metadata,
    )
    record_audit_event(
        actor=actor,
        action="application.entrance_submitted",
        target_type="program_application",
        target_id=str(application.pk),
        metadata={"old_status": old_status, **(metadata or {})},
    )


def invite_to_entrance_exam(application, actor) -> None:
    """Transition an activated diploma application to entrance invitation."""
    if application.status not in (PROGRAM_APP_STATUS_SUBMITTED, PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED):
        raise ValueError(_("Application must be awaiting admin review before invitation."))
    if application.program_type != PROGRAM_TYPE_DIPLOMA:
        raise ValueError(_("Only diploma applications can be invited to entrance exam."))
    _advance_to(application, PROGRAM_APP_STATUS_INVITED, DECISION_ACTION_INVITED_TO_ENTRANCE, actor)


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
    if application.program_type == PROGRAM_TYPE_COURSE:
        allowed = {PROGRAM_APP_STATUS_SUBMITTED, PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED, PROGRAM_APP_STATUS_PAYMENT_PENDING}
    else:
        allowed = {
            PROGRAM_APP_STATUS_INVITED,
            PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED,
            PROGRAM_APP_STATUS_INTERVIEW_REQUESTED,
        }
    if application.status not in allowed:
        raise ValueError(_("Application cannot be accepted from current status."))
    old_status = application.status
    application.accept(actor, notes=notes)
    _record_decision(application, DECISION_ACTION_ACCEPT, old_status, PROGRAM_APP_STATUS_ACCEPTED, actor)
    record_audit_event(
        actor=actor,
        action="application.approved",
        target_type="program_application",
        target_id=str(application.pk),
        metadata={"student_id": application.student_id, "send_email": send_email},
    )
    if send_email:
        transaction.on_commit(lambda: _send_program_app_acceptance_email(application, actor=actor))

    # Trigger intake request for course applications that are already paid
    if application.program_type == PROGRAM_TYPE_COURSE:
        if has_application_paid_record(application):
            _attempt_intake_request_safe(application, actor=actor)
        else:
            send_post_activation_application_email(application, actor=actor)


@transaction.atomic
def reject_program_application(application, actor, reason: str, notes: str = "", send_email: bool = False) -> None:
    """Reject a course or diploma from any active pre-decision admissions state."""
    if not reason.strip():
        raise ValueError(_("Rejection reason is required."))
    allowed = {
        PROGRAM_APP_STATUS_INVITED,
        PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED,
        PROGRAM_APP_STATUS_INTERVIEW_REQUESTED,
    }
    if application.program_type == PROGRAM_TYPE_COURSE:
        allowed |= {PROGRAM_APP_STATUS_SUBMITTED, PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED, PROGRAM_APP_STATUS_PAYMENT_PENDING}
    if application.status not in allowed:
        raise ValueError(_("Application cannot be rejected from current status."))
    old_status = application.status
    application.reject(actor, reason=reason, notes=notes)
    _record_decision(application, DECISION_ACTION_REJECT, old_status, PROGRAM_APP_STATUS_REJECTED, actor, reason=reason)
    record_audit_event(
        actor=actor,
        action="application.rejected",
        target_type="program_application",
        target_id=str(application.pk),
        metadata={"reason": reason, "student_id": application.student_id, "send_email": send_email},
    )
    if send_email:
        transaction.on_commit(lambda: _send_program_app_rejection_email(application, reason, actor=actor))


@transaction.atomic
def request_interview_program_application(application, actor, *, interview_at=None, phone_number="", expires_at, notes="", send_email=True) -> None:
    """Transition entrance_submitted -> interview_requested."""
    if application.status != PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED:
        raise ValueError(_("Interview can only be requested for applications with submitted entrance exam."))
    if application.program_type != PROGRAM_TYPE_DIPLOMA:
        raise ValueError(_("Interview can only be requested for diploma applications."))
    if not interview_at and not phone_number:
        raise ValueError(_("Either interview date/time or staff phone instructions is required."))
    if not expires_at:
        raise ValueError(_("Expiry time is required for interview request."))
    if timezone.is_naive(expires_at):
        raise ValueError(_("Expiry time must be timezone-aware."))
    if expires_at <= timezone.now():
        raise ValueError(_("Expiry time must be in the future."))

    old_status = application.status
    _set_status(application, PROGRAM_APP_STATUS_INTERVIEW_REQUESTED)
    metadata = {
        "interview_at": interview_at.isoformat() if interview_at else None,
        "phone_number": phone_number,
        "expires_at": expires_at.isoformat(),
    }
    _record_decision(
        application, DECISION_ACTION_REQUEST_INTERVIEW,
        old_status, PROGRAM_APP_STATUS_INTERVIEW_REQUESTED, actor,
        metadata=metadata,
    )
    if notes:
        record_program_application_note(application, actor, note=notes, metadata=metadata)
    record_audit_event(
        actor=actor,
        action="application.interview_requested",
        target_type="program_application",
        target_id=str(application.pk),
        metadata=metadata,
    )
    if send_email:
        transaction.on_commit(lambda: _send_interview_request_email(application, metadata))


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


def revert_program_application_decision(application, actor, reason: str = "") -> None:
    if application.entrance_attempts.exists():
        raise ValueError(_("You cannot revert an application after an entrance exam attempt exists."))

    last_decision = application.decisions.exclude(action=DECISION_ACTION_REVERT).order_by("-created_at").first()
    if not last_decision:
        raise ValueError(_("No decision to revert."))

    with transaction.atomic():
        old_status = application.status
        application.status = last_decision.old_status
        application.reviewed_by = actor
        application.reviewed_at = timezone.now()
        application.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
        _record_decision(application, DECISION_ACTION_REVERT, old_status, application.status, actor, reason=reason)
        record_audit_event(
            actor=actor,
            action=ACTION_APPLICATION_DECISION_REVERTED,
            target_type="program_application",
            target_id=str(application.pk),
            metadata={"student_id": application.student_id, "reason": reason},
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
    if application.status not in ENTRANCE_DECISION_STATUSES:
        raise ValueError(_("The decision can no longer be changed for this application."))
    if target not in ENTRANCE_DECISION_STATUSES:
        raise ValueError(_("Invalid decision target."))
    if target == application.status:
        raise ValueError(_("The application already has this decision."))
    if target == PROGRAM_APP_STATUS_REJECTED and not (reason or "").strip():
        raise ValueError(_("Rejection reason is required."))
    if target == PROGRAM_APP_STATUS_INTERVIEW_REQUESTED:
        if application.program_type != PROGRAM_TYPE_DIPLOMA:
            raise ValueError(_("Interview can only be requested for diploma applications."))
        if not interview_at and not phone_number:
            raise ValueError(_("Either interview date/time or staff phone instructions is required."))
        if not expires_at:
            raise ValueError(_("Expiry time is required for interview request."))
        if timezone.is_naive(expires_at):
            raise ValueError(_("Expiry time must be timezone-aware."))
        if expires_at <= timezone.now():
            raise ValueError(_("Expiry time must be in the future."))

    old_status = application.status
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
    application.status = target
    application.reviewed_by = actor
    application.reviewed_at = timezone.now()
    if notes:
        application.admin_notes = notes
    application.save(update_fields=["status", "reviewed_by", "reviewed_at", "admin_notes", "updated_at"])
    _record_decision(application, decision_action, old_status, target, actor, reason=reason, metadata=metadata)
    if notes:
        record_program_application_note(application, actor, note=notes, metadata=metadata)
    record_audit_event(
        actor=actor,
        action=audit_action,
        target_type="program_application",
        target_id=str(application.pk),
        metadata={"old_status": old_status, "new_status": target, "reason": reason, "send_email": send_email, **metadata},
    )
    if send_email:
        if target == PROGRAM_APP_STATUS_ACCEPTED:
            transaction.on_commit(lambda: _send_program_app_acceptance_email(application, actor=actor))
        elif target == PROGRAM_APP_STATUS_REJECTED:
            transaction.on_commit(lambda: _send_program_app_rejection_email(application, reason, actor=actor))
        elif target == PROGRAM_APP_STATUS_INTERVIEW_REQUESTED:
            transaction.on_commit(lambda: _send_interview_request_email(application, metadata))


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
    return application_documents.has_application_documents_submitted(application)


def has_application_paid_record(application) -> bool:
    """Check if a paid, non-deleted PaymentRecord exists for this application."""
    return PaymentRecord.objects.filter(
        application=application,
        status=PAYMENT_STATUS_PAID,
        deleted_at__isnull=True,
    ).exists()


def has_active_application_waiver(application) -> bool:
    """Check if an active Admin payment waiver exists for this application."""
    return PaymentWaiver.objects.filter(
        application=application,
        status=WAIVER_STATUS_ACTIVE,
    ).exists()


def has_application_payment_readiness(application) -> bool:
    """Payment readiness: satisfied payment OR a valid recorded Admin waiver."""
    return has_application_paid_record(application) or has_active_application_waiver(application)


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
    if application.status == PROGRAM_APP_STATUS_DOCUMENTS_SUBMITTED and not application.submitted_document_upload:
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

    old_status = locked.status
    restored_status = activation.old_status
    enrollment_id = metadata.get("enrollment_id")
    enrollment_type = metadata.get("enrollment_type")
    enrollment_created = metadata.get("enrollment_created")
    if enrollment_created is None and enrollment_id:
        # Older activation decisions did not persist this flag. The timestamp
        # fallback only treats an enrollment created immediately before that
        # activation as belonging to the activation being rolled back.
        if enrollment_type == "diploma":
            legacy_enrollment = DiplomaEnrollment.objects.filter(pk=enrollment_id).first()
        elif enrollment_type == "course":
            legacy_enrollment = CourseEnrollment.objects.filter(pk=enrollment_id).first()
        else:
            legacy_enrollment = None
        enrollment_created = bool(
            legacy_enrollment
            and legacy_enrollment.created_at >= activation.created_at - timedelta(seconds=10)
        )

    enrollment = None
    if enrollment_created and enrollment_id:
        if enrollment_type == "diploma":
            enrollment = DiplomaEnrollment.objects.select_for_update().filter(pk=enrollment_id).first()
        elif enrollment_type == "course":
            enrollment = CourseEnrollment.objects.select_for_update().filter(pk=enrollment_id).first()
        if enrollment:
            enrollment.status = ENROLLMENT_STATUS_PENDING
            enrollment.activated_at = None
            enrollment.expires_at = None
            enrollment.save(update_fields=["status", "activated_at", "expires_at", "updated_at"])

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
            "enrollment_id": enrollment_id,
            "enrollment_type": enrollment_type,
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
            "enrollment_id": enrollment_id,
            "enrollment_reset": enrollment is not None,
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
# source so the buttons shown always match the exact gates the mutation
# services enforce. Each status set mirrors the gate of the corresponding
# transition function:
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
# The mutation services keep their own validation: this projection only
# drives display, and the backend re-checks every transition on mutation.

ACTION_ACCEPT = "accept"
ACTION_REJECT = "reject"
ACTION_MARK_PAID = "mark_paid"
ACTION_REQUEST_INTAKE = "request_intake"
ACTION_REQUEST_DOCUMENTS = "request_documents"
ACTION_REQUEST_INTERVIEW = "request_interview"
ACTION_INVITE_TO_ENTRANCE = "invite_to_entrance"
ACTION_GRANT_WAIVER = "grant_waiver"
ACTION_ACTIVATE = "activate"

_ACCEPT_ALLOWED_STATUSES = {
    PROGRAM_TYPE_COURSE: frozenset({
        PROGRAM_APP_STATUS_SUBMITTED,
        PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED,
        PROGRAM_APP_STATUS_PAYMENT_PENDING,
    }),
    PROGRAM_TYPE_DIPLOMA: frozenset({
        PROGRAM_APP_STATUS_INVITED,
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


def allowed_application_actions(application) -> frozenset[str]:
    """Return the set of valid action keys for an application's current state.

    The single source of truth for the admin detail page. Every allowed status
    set matches the exact gate enforced by the corresponding mutation service
    below, and each mutation service re-checks the transition when called.
    """
    status = application.status
    program_type = application.program_type
    allowed: set[str] = set()
    waiver_statuses = _waiver_legal_application_statuses()
    payment_action_status = status in LEGAL_PAYMENT_STATUSES or status in waiver_statuses
    documents_gate_satisfied = (
        program_type != PROGRAM_TYPE_DIPLOMA
        or not payment_action_status
        or has_application_documents_submitted(application)
    )
    intake_gate_satisfied = (
        not payment_action_status
        or has_required_intake_response(application)
    )

    if status in _ACCEPT_ALLOWED_STATUSES.get(program_type, frozenset()):
        allowed.add(ACTION_ACCEPT)
    if status in _REJECT_ALLOWED_STATUSES.get(program_type, frozenset()):
        allowed.add(ACTION_REJECT)
    if status in LEGAL_PAYMENT_STATUSES and documents_gate_satisfied and intake_gate_satisfied:
        allowed.add(ACTION_MARK_PAID)
    if status in INTAKE_REQUESTABLE_STATUSES:
        allowed.add(ACTION_REQUEST_INTAKE)
    if program_type == PROGRAM_TYPE_DIPLOMA and status in _REQUEST_DOCUMENTS_ALLOWED_STATUSES:
        allowed.add(ACTION_REQUEST_DOCUMENTS)
    if program_type == PROGRAM_TYPE_DIPLOMA and status == PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED:
        allowed.add(ACTION_REQUEST_INTERVIEW)
    if program_type == PROGRAM_TYPE_DIPLOMA and status in _INVITE_TO_ENTRANCE_ALLOWED_STATUSES:
        allowed.add(ACTION_INVITE_TO_ENTRANCE)
    if status in waiver_statuses and documents_gate_satisfied and intake_gate_satisfied:
        allowed.add(ACTION_GRANT_WAIVER)
    if can_activate_application(application).ready:
        allowed.add(ACTION_ACTIVATE)

    return frozenset(allowed)


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
    """Create a paid PaymentRecord linked to the application and attempt activation.

    Returns the PaymentRecord.
    """
    locked = ProgramApplication.objects.select_for_update().get(pk=application.pk)
    if locked.status == PROGRAM_APP_STATUS_REJECTED:
        raise ValueError(_("Cannot record payment for a rejected application."))
    if locked.status == PROGRAM_APP_STATUS_ACTIVATED:
        raise ValueError(_("Application is already activated."))
    if locked.status not in LEGAL_PAYMENT_STATUSES:
        raise ValueError(_("Cannot record payment in current application status."))
    if locked.program_type == PROGRAM_TYPE_DIPLOMA and not has_application_documents_submitted(locked):
        raise ValueError(_("Application documents must be submitted before recording payment."))
    if not has_required_intake_response(locked):
        raise ValueError(_("Required intake form must be submitted before recording payment."))
    # Guard: duplicate payment (exclude soft-deleted records)
    if PaymentRecord.objects.filter(
        student=locked.student, application=locked, status=PAYMENT_STATUS_PAID, deleted_at__isnull=True
    ).exists():
        raise ValueError(_("Payment already recorded for this application."))

    if paid_at is None:
        paid_at = timezone.now()

    # Validate amount
    amount_dec = validate_score(amount or 0)

    record = PaymentRecord.objects.create(
        student=locked.student,
        application=locked,
        amount=amount_dec,
        currency=CURRENCY_EGP,
        status=PAYMENT_STATUS_PAID,
        paid_at=paid_at,
        external_reference=external_reference,
        notes=notes,
        imported_by=actor,
        target_type=locked.program_type,
        target_snapshot=_build_application_snapshot(locked),
    )

    # Attempt activation — idempotent, may not be ready
    result = activate_application_if_ready(application, actor=actor)
    # Trigger intake request if activation blocked by missing intake
    if not result.activated and "intake_form_required" in result.reasons:
        _attempt_intake_request_safe(application, actor=actor)
    record_audit_event(
        actor=actor,
        action="application.paid",
        target_type="program_application",
        target_id=str(application.pk),
        metadata={"amount": str(amount_dec), "external_reference": external_reference, "payment_record_id": str(record.pk)},
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
