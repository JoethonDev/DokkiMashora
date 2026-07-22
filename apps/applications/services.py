"""Application business logic: duplicate checking, phone normalization, submission, and state machine."""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import date
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
from apps.attendance.services import get_or_create_attendance_code_for_enrollment
from apps.audit.constants import ACTION_APPLICATION_DECISION_REVERTED
from apps.audit.services import record_audit_event
from apps.communications.constants import (
    TEMPLATE_ACCEPTANCE,
    TEMPLATE_REJECTION,
)
from apps.communications.services import send_templated_email
from apps.core.permissions import validate_score
from apps.intake_forms.selectors import has_required_intake_response  # noqa: E402
from apps.payments.constants import CURRENCY_EGP, PAYMENT_STATUS_PAID
from apps.payments.models import PaymentRecord
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
    DECISION_ACTION_REJECT,
    DECISION_ACTION_REQUEST_INTERVIEW,
    DECISION_ACTION_REVERT,
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

COUNTRY_CODES = {
    "20": "EG",
    "966": "SA",
    "971": "AE",
    "965": "KW",
    "974": "QA",
    "973": "BH",
    "968": "OM",
    "962": "JO",
    "961": "LB",
    "963": "SY",
    "964": "IQ",
    "970": "PS",
    "212": "MA",
    "213": "DZ",
    "216": "TN",
    "218": "LY",
    "249": "SD",
}

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
    age: int
    country_code: str = COUNTRY_EG
    program_type: str | None = None
    cohort_id: int | None = None
    course_id: int | None = None


def normalize_phone(raw: str) -> str:
    """Strip the country code, +, 00, and leading 0 from a phone number."""
    digits = "".join(c for c in raw if c.isdigit())
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    for code in sorted(COUNTRY_CODES.keys(), key=len, reverse=True):
        if digits.startswith(code) and len(digits) > len(code) + 6:
            digits = digits[len(code):]
            break
    if digits.startswith("0"):
        digits = digits[1:]
    return digits


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
    normalized_input = normalize_phone(data.phone)
    if normalized_input:
        for user in User.objects.exclude(phone=""):
            if normalize_phone(user.phone) == normalized_input:
                return "phone"
    return None


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

    user_password = normalize_phone(data.phone) or secrets.token_urlsafe(8)
    normalized_id = normalize_identity_number(data.country_code, data.national_id)
    user = User.objects.create_user(
        email=data.email,
        password=user_password,
        full_name=data.full_name,
        phone=data.phone,
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
        phone=data.phone,
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
        cohort = Cohort.objects.get(pk=data.cohort_id)
        program_application = ProgramApplication.objects.create(
            student=profile,
            program_type=PROGRAM_TYPE_DIPLOMA,
            cohort=cohort,
            status=PROGRAM_APP_STATUS_SUBMITTED,
        )
    elif data.program_type == PROGRAM_TYPE_COURSE and data.course_id:
        course = Course.objects.get(pk=data.course_id)
        program_application = ProgramApplication.objects.create(
            student=profile,
            program_type=PROGRAM_TYPE_COURSE,
            course=course,
            status=PROGRAM_APP_STATUS_SUBMITTED,
        )

    transaction.on_commit(lambda: send_activation_email(user, user_password))

    return profile, program_application


def submit_program_application(
    profile: StudentProfile,
    program_type: str,
    cohort: Cohort | None = None,
    course: Course | None = None,
) -> ProgramApplication:
    """Create a new program application for an existing student."""
    if program_type == PROGRAM_TYPE_COURSE:
        if course is None or not course.is_active or not course.versions.filter(is_active=True).exists():
            raise ValueError(_("This course has no active version available for enrollment."))
    _check_duplicate_program_application(profile, program_type, cohort=cohort, course=course)

    return ProgramApplication.objects.create(
        student=profile,
        program_type=program_type,
        cohort=cohort,
        course=course,
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
    group, _ = Group.objects.get_or_create(name=GROUP_STUDENT)
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
        status="sent",
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

    from apps.intake_forms.selectors import get_required_intake_form_for_application
    from apps.intake_forms.services import ensure_required_intake_request

    form = get_required_intake_form_for_application(application)
    if form and not has_required_intake_response(application):
        ensure_required_intake_request(application, actor=actor)
        return

    if application.program_type == PROGRAM_TYPE_COURSE:
        email_action = "course_payment_instructions"
        if not force_resend and _has_non_resend_email_log(application, email_action):
            return
        transaction.on_commit(lambda: _send_course_payment_instructions_email(application, actor))
        _log_email_send(application, email_action, "course_payment_instructions", application.student.user.email, actor)
        _advance_to(application, PROGRAM_APP_STATUS_PAYMENT_PENDING, DECISION_ACTION_PAYMENT_PENDING, actor)

    elif application.program_type == PROGRAM_TYPE_DIPLOMA:
        if application.status == PROGRAM_APP_STATUS_SURVEY_SUBMITTED:
            _advance_to(application, PROGRAM_APP_STATUS_PAYMENT_PENDING, DECISION_ACTION_PAYMENT_PENDING, actor)
            return
        email_action = "diploma_entrance_exam_schedule"
        if not force_resend and _has_non_resend_email_log(application, email_action):
            return
        transaction.on_commit(lambda: _send_diploma_entrance_schedule_email(application, actor))
        _log_email_send(application, email_action, "diploma_entrance_exam_schedule", application.student.user.email, actor)
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
    send_templated_email("course_payment_instructions", email, context, sent_by=actor)


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
    send_templated_email("diploma_entrance_exam_schedule", email, context, sent_by=actor)


def _log_email_send(application, action, template_slug, recipient, actor=None) -> None:
    ProgramApplicationEmailLog.objects.create(
        program_application=application,
        action=action,
        template_slug=template_slug,
        recipient=recipient,
        sent_by=actor,
        sent_at=timezone.now(),
        status="sent",
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
    from apps.intake_forms.services import ensure_required_intake_request

    try:
        ensure_required_intake_request(application, actor=actor)
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
    """Accept from entrance_submitted or interview_requested (diploma) or payment_pending (course)."""
    if application.program_type == PROGRAM_TYPE_COURSE:
        allowed = {PROGRAM_APP_STATUS_SUBMITTED, PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED, PROGRAM_APP_STATUS_PAYMENT_PENDING}
    else:
        allowed = {PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED, PROGRAM_APP_STATUS_INTERVIEW_REQUESTED}
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
        transaction.on_commit(lambda: _send_program_app_acceptance_email(application))

    # Trigger intake request for course applications that are already paid
    if application.program_type == PROGRAM_TYPE_COURSE:
        if has_application_paid_record(application):
            _attempt_intake_request_safe(application, actor=actor)
        else:
            send_post_activation_application_email(application, actor=actor)


@transaction.atomic
def reject_program_application(application, actor, reason: str, notes: str = "", send_email: bool = False) -> None:
    """Reject from entrance_submitted or interview_requested (diploma) or submitted/account_activated/payment_pending (course)."""
    if not reason.strip():
        raise ValueError(_("Rejection reason is required."))
    allowed = {PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED, PROGRAM_APP_STATUS_INTERVIEW_REQUESTED}
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
        transaction.on_commit(lambda: _send_program_app_rejection_email(application, reason))


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
    send_templated_email("diploma_interview_request", email, context, sent_by=None)
    _log_email_send(prog_app, "diploma_interview_request", "diploma_interview_request", email)


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
    Wrapped in a single transaction so a failure in one application's
    transition rolls back all.
    """
    if not hasattr(user, "student_profile"):
        return
    profile = user.student_profile
    applications = ProgramApplication.objects.filter(
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
    from .documents import has_application_documents_submitted as _check
    return _check(application)


def has_application_paid_record(application) -> bool:
    """Check if a paid, non-deleted PaymentRecord exists for this application."""
    return PaymentRecord.objects.filter(
        application=application,
        status=PAYMENT_STATUS_PAID,
        deleted_at__isnull=True,
    ).exists()


def can_activate_application(application) -> ActivationCheck:
    """Determine whether an application is ready for activation.

    This is a read-only check — no mutations.
    Phase 5: required intake form check added.
    """
    reasons: list[str] = []

    if not application.student.user.email_activated_at:
        reasons.append("account_not_activated")

    if application.program_type == PROGRAM_TYPE_COURSE:
        if application.status not in (PROGRAM_APP_STATUS_ACCEPTED, PROGRAM_APP_STATUS_PAYMENT_PENDING):
            reasons.append("not_accepted")
        if not application.course:
            reasons.append("no_course_target")
        if not has_application_paid_record(application):
            reasons.append("not_paid")
        # Phase 5: required intake check
        if not _has_required_intake_check(application):
            reasons.append("intake_form_required")
        return ActivationCheck(ready=len(reasons) == 0, reasons=reasons)

    # Diploma
    if application.status not in (PROGRAM_APP_STATUS_DOCUMENTS_SUBMITTED, PROGRAM_APP_STATUS_PAYMENT_PENDING, PROGRAM_APP_STATUS_ACTIVATED):
        reasons.append("not_accepted_or_documents_not_submitted")
    if application.status == PROGRAM_APP_STATUS_DOCUMENTS_SUBMITTED and not application.submitted_document_upload:
        reasons.append("documents_not_uploaded")
    if not application.cohort:
        reasons.append("no_cohort_target")
    if not has_application_paid_record(application):
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


def _send_program_app_acceptance_email(prog_app) -> None:
    email = prog_app.student.user.email
    if not email:
        return
    context = {
        "full_name": prog_app.student.full_name or "",
        "cohort_name": prog_app.cohort.name if prog_app.cohort else "",
        "diploma_title": prog_app.cohort.diploma.title if prog_app.cohort and prog_app.cohort.diploma else "",
        "program_type": prog_app.program_type or "",
    }
    send_templated_email(TEMPLATE_ACCEPTANCE, email, context, sent_by=None)


def _send_program_app_rejection_email(prog_app, reason: str) -> None:
    email = prog_app.student.user.email
    if not email:
        return
    context = {
        "full_name": prog_app.student.full_name or "",
        "rejection_reason": reason,
        "cohort_name": prog_app.cohort.name if prog_app.cohort else "",
    }
    send_templated_email(TEMPLATE_REJECTION, email, context, sent_by=None)
