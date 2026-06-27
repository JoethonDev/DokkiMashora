"""Application business logic: duplicate checking, phone normalization, submission."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from django.utils.translation import gettext as _

from apps.academics.models import Cohort, Course
from apps.academics.models import Cohort as CohortModel
from apps.academics.models import Course as CourseModel
from apps.accounts.constants import GROUP_STUDENT
from apps.audit.constants import (
    ACTION_APPLICATION_ACTIVATED,
    ACTION_APPLICATION_APPROVED,
    ACTION_APPLICATION_DECISION_REVERTED,
    ACTION_APPLICATION_REJECTED,
    ACTION_APPLICATION_STATUS_CHANGE,
)
from apps.audit.services import record_audit_event
from apps.communications.constants import (
    TEMPLATE_ACCEPTANCE,
    TEMPLATE_ACTIVATION,
    TEMPLATE_REGISTRATION_RECEIVED,
    TEMPLATE_REJECTION,
)
from apps.communications.services import send_templated_email
from apps.payments.constants import PAYMENT_STATUS_UNPAID
from apps.payments.models import PaymentRecord
from apps.students.models import StudentProfile

from .constants import (
    APP_STATUS_PENDING,
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_ACTIVATED,
    PROGRAM_APP_STATUS_SUBMITTED,
    PROGRAM_TYPE_COURSE,
    PROGRAM_TYPE_DIPLOMA,
)
from .models import REVERTABLE_ACTIONS, Application, ApplicationDecision, ProgramApplication

if TYPE_CHECKING:
    from apps.academics.models import Cohort, Course

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
    program_type: str | None = None
    cohort_id: int | None = None
    course_id: int | None = None


def normalize_phone(raw: str) -> str:
    """Strip the country code, +, 00, and leading 0 from a phone number.

    Examples:
        +201234567890 -> 1234567890
        00201234567890 -> 1234567890
        0100 123 4567  -> 1001234567
        +1 555-123-4567 -> 5551234567
    """
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

    Check order per guardrails: national ID -> email -> phone.
    """
    if StudentProfile.objects.filter(national_id=data.national_id).exists():
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
) -> tuple[StudentProfile, Application, ProgramApplication | None]:
    """Create a user, profile, root application, and optional program request.

    Raises ValueError with a duplicate field name if the data conflicts
    with an existing record.
    """
    duplicate_on = find_duplicate(data)
    if duplicate_on:
        raise ValueError(duplicate_on)

    user_password = normalize_phone(data.phone)
    user = User.objects.create_user(
        email=data.email,
        password=user_password,
        full_name=data.full_name,
        phone=data.phone,
        national_id=data.national_id,
    )
    user.is_active = False
    user.save(update_fields=["is_active", "updated_at"])
    user.groups.add(_student_group())

    profile = StudentProfile.objects.create(
        user=user,
        full_name=data.full_name,
        date_of_birth=data.date_of_birth,
        gender=data.gender,
        address=data.address,
        phone=data.phone,
        national_id=data.national_id,
        education=data.education,
        specialization=data.specialization,
        employer=data.employer,
        current_job=data.current_job,
        church=data.church,
        priest_name=data.priest_name,
        priest_contact=data.priest_contact,
    )
    root_application = Application.objects.create(student=profile, status=APP_STATUS_PENDING)

    program_application = None
    if data.program_type == PROGRAM_TYPE_DIPLOMA and data.cohort_id:
        cohort = Cohort.objects.get(pk=data.cohort_id)
        program_application = ProgramApplication.objects.create(
            root_application=root_application,
            student=profile,
            program_type=PROGRAM_TYPE_DIPLOMA,
            cohort=cohort,
            status=PROGRAM_APP_STATUS_SUBMITTED,
        )
    elif data.program_type == PROGRAM_TYPE_COURSE and data.course_id:
        course = Course.objects.get(pk=data.course_id)
        program_application = ProgramApplication.objects.create(
            root_application=root_application,
            student=profile,
            program_type=PROGRAM_TYPE_COURSE,
            course=course,
            status=PROGRAM_APP_STATUS_SUBMITTED,
        )

    _send_registration_received_email(profile, root_application, program_application)

    return profile, root_application, program_application


def submit_program_application(
    profile: StudentProfile,
    program_type: str,
    cohort: Cohort | None = None,
    course: Course | None = None,
) -> ProgramApplication:
    """Create a new program application for an existing student.

    Enforces:
    - No duplicate pending/active program application for the same target.
    - Student must have a root Application record.
    """
    root_application = Application.objects.filter(student=profile).order_by("-created_at").first()
    if root_application is None:
        root_application = Application.objects.create(student=profile, status=APP_STATUS_PENDING)

    _check_duplicate_program_application(profile, program_type, cohort=cohort, course=course)

    return ProgramApplication.objects.create(
        root_application=root_application,
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
        if not isinstance(cohort, CohortModel):
            cohort = CohortModel.objects.get(pk=cohort)
        existing = ProgramApplication.objects.filter(
            student=profile,
            program_type=PROGRAM_TYPE_DIPLOMA,
            cohort=cohort,
        ).exclude(
            status="rejected",
        ).exists()
        if existing:
            raise ValueError(
                _("You already have a pending or active application for this diploma cohort.")
            )

    elif program_type == PROGRAM_TYPE_COURSE and course is not None:
        if not isinstance(course, CourseModel):
            course = CourseModel.objects.get(pk=course)
        existing = ProgramApplication.objects.filter(
            student=profile,
            program_type=PROGRAM_TYPE_COURSE,
            course=course,
        ).exclude(
            status="rejected",
        ).exists()
        if existing:
            raise ValueError(
                _("You already have a pending or active application for this course.")
            )


def _student_group():
    group, _ = Group.objects.get_or_create(name=GROUP_STUDENT)
    return group


# ---------------------------------------------------------------------------
# Decision services (create decision history + audit events)
# ---------------------------------------------------------------------------


def _record_decision(
    target_type: str,
    target_id: int,
    action: str,
    old_status: str,
    new_status: str,
    actor,
    reason: str = "",
) -> ApplicationDecision:
    return ApplicationDecision.objects.create(
        target_type=target_type,
        target_id=str(target_id),
        action=action,
        old_status=old_status,
        new_status=new_status,
        actor=actor,
        reason=reason,
    )


def _activate_student_user(profile: StudentProfile) -> None:
    group, _ = Group.objects.get_or_create(name=GROUP_STUDENT)
    profile.user.groups.add(group)
    profile.user.is_active = True
    profile.user.save(update_fields=["is_active", "updated_at"])


def _send_registration_received_email(profile, root_application=None, program_application=None):
    email = profile.user.email
    context = {
        "full_name": profile.full_name,
        "email": email,
        "national_id": profile.national_id or "",
        "phone": profile.phone or "",
        "date_of_birth": profile.date_of_birth.isoformat() if profile.date_of_birth else "",
        "gender": profile.gender or "",
        "education": profile.education or "",
        "church": profile.church or "",
        "priest_name": profile.priest_name or "",
        "program_type": "",
        "course_title": "",
        "diploma_title": "",
    }
    if program_application:
        context["program_type"] = program_application.program_type or ""
        if program_application.cohort_id and program_application.cohort:
            context["diploma_title"] = program_application.cohort.diploma.title or ""
        if program_application.course:
            context["course_title"] = program_application.course.title or ""
    send_templated_email(TEMPLATE_REGISTRATION_RECEIVED, email, context, sent_by=None)


def _get_program_app(application):
    return application.program_applications.filter(
        status__in=[PROGRAM_APP_STATUS_ACCEPTED, PROGRAM_APP_STATUS_ACTIVATED]
    ).first()


def _send_acceptance_email(application):
    email = application.student.user.email
    prog_app = _get_program_app(application)
    context = {
        "full_name": application.student.full_name,
        "cohort_name": prog_app.cohort.name if prog_app and prog_app.cohort else "",
        "activation_url": "",
        "diploma_title": prog_app.cohort.diploma.title if prog_app and prog_app.cohort and prog_app.cohort.diploma else "",
        "program_type": prog_app.program_type if prog_app else "",
    }
    send_templated_email(TEMPLATE_ACCEPTANCE, email, context, sent_by=None)


def _send_rejection_email(application, reason: str):
    email = application.student.user.email
    prog_app = _get_program_app(application)
    context = {
        "full_name": application.student.full_name,
        "rejection_reason": reason,
        "cohort_name": prog_app.cohort.name if prog_app and prog_app.cohort else "",
    }
    send_templated_email(TEMPLATE_REJECTION, email, context, sent_by=None)


def _send_activation_email(application):
    email = application.student.user.email
    prog_app = _get_program_app(application)
    context = {
        "full_name": application.student.full_name,
        "activation_url": "",
        "cohort_name": prog_app.cohort.name if prog_app and prog_app.cohort else "",
        "email": email,
        "phone": application.student.phone or "",
        "diploma_title": prog_app.cohort.diploma.title if prog_app and prog_app.cohort and prog_app.cohort.diploma else "",
    }
    send_templated_email(TEMPLATE_ACTIVATION, email, context, sent_by=None)


def _create_payment_record_on_activation(application, actor):
    PaymentRecord.objects.create(
        student=application.student,
        application=application,
        amount=0,
        status=PAYMENT_STATUS_UNPAID,
        imported_by=actor,
    )


def approve_application(application, actor, notes: str = "") -> None:
    old_status = application.status
    application.approve(actor, notes=notes)
    _record_decision("application", application.pk, "approve", old_status, application.status, actor)
    record_audit_event(
        actor=actor,
        action=ACTION_APPLICATION_APPROVED,
        target_type="application",
        target_id=str(application.pk),
        metadata={"student_id": application.student_id},
    )
    _send_acceptance_email(application)


def reject_application(application, actor, reason: str, notes: str = "") -> None:
    old_status = application.status
    application.reject(actor, reason=reason, notes=notes)
    _record_decision("application", application.pk, "reject", old_status, application.status, actor, reason=reason)
    record_audit_event(
        actor=actor,
        action=ACTION_APPLICATION_REJECTED,
        target_type="application",
        target_id=str(application.pk),
        metadata={"reason": reason, "student_id": application.student_id},
    )
    _send_rejection_email(application, reason)


def mark_review_application(application, actor) -> None:
    old_status = application.status
    application.mark_under_review(actor)
    _record_decision("application", application.pk, "mark_review", old_status, application.status, actor)
    record_audit_event(
        actor=actor,
        action=ACTION_APPLICATION_STATUS_CHANGE,
        target_type="application",
        target_id=str(application.pk),
        metadata={"status": application.status},
    )


def activate_application(application, actor) -> None:
    old_status = application.status
    application.activate()
    _activate_student_user(application.student)
    _record_decision("application", application.pk, "activate", old_status, application.status, actor)
    record_audit_event(
        actor=actor,
        action=ACTION_APPLICATION_ACTIVATED,
        target_type="application",
        target_id=str(application.pk),
        metadata={"student_id": application.student_id, "user_id": application.student.user_id},
    )
    _send_activation_email(application)
    _create_payment_record_on_activation(application, actor)


def revert_application_decision(application, actor, reason: str = "") -> None:
    last = (
        ApplicationDecision.objects.filter(target_type="application", target_id=str(application.pk))
        .exclude(action="revert")
        .order_by("-created_at")
        .first()
    )
    if last is None or last.action not in REVERTABLE_ACTIONS:
        raise ValueError(_("No revertable decision found for this application."))
    old_status = application.status
    application.status = last.old_status
    application.rejection_reason = ""
    application.save(update_fields=["status", "rejection_reason", "updated_at"])
    _record_decision("application", application.pk, "revert", old_status, application.status, actor, reason=reason)
    record_audit_event(
        actor=actor,
        action=ACTION_APPLICATION_DECISION_REVERTED,
        target_type="application",
        target_id=str(application.pk),
        metadata={"old_status": old_status, "new_status": application.status, "reason": reason},
    )


# ---------------------------------------------------------------------------
# ProgramApplication decision services
# ---------------------------------------------------------------------------


def approve_program_application(application, actor, notes: str = "") -> None:
    old_status = application.status
    application.accept(actor, notes=notes)
    _record_decision("program_application", application.pk, "approve", old_status, application.status, actor)
    record_audit_event(
        actor=actor,
        action=ACTION_APPLICATION_APPROVED,
        target_type="program_application",
        target_id=str(application.pk),
        metadata={"student_id": application.student_id},
    )


def reject_program_application(application, actor, reason: str, notes: str = "") -> None:
    old_status = application.status
    application.reject(actor, reason=reason, notes=notes)
    _record_decision("program_application", application.pk, "reject", old_status, application.status, actor, reason=reason)
    record_audit_event(
        actor=actor,
        action=ACTION_APPLICATION_REJECTED,
        target_type="program_application",
        target_id=str(application.pk),
        metadata={"reason": reason, "student_id": application.student_id},
    )


def mark_review_program_application(application, actor) -> None:
    old_status = application.status
    application.mark_under_review(actor)
    _record_decision("program_application", application.pk, "mark_review", old_status, application.status, actor)
    record_audit_event(
        actor=actor,
        action=ACTION_APPLICATION_STATUS_CHANGE,
        target_type="program_application",
        target_id=str(application.pk),
        metadata={"status": application.status},
    )


def invite_program_application(application, actor) -> None:
    old_status = application.status
    application.mark_invited(actor)
    _record_decision("program_application", application.pk, "invite", old_status, application.status, actor)
    record_audit_event(
        actor=actor,
        action=ACTION_APPLICATION_STATUS_CHANGE,
        target_type="program_application",
        target_id=str(application.pk),
        metadata={"status": application.status},
    )


def activate_program_application(application, actor) -> None:
    old_status = application.status
    application.activate()
    _activate_student_user(application.student)
    _record_decision("program_application", application.pk, "activate", old_status, application.status, actor)
    record_audit_event(
        actor=actor,
        action=ACTION_APPLICATION_ACTIVATED,
        target_type="program_application",
        target_id=str(application.pk),
        metadata={"student_id": application.student_id, "user_id": application.student.user_id},
    )


def revert_program_application_decision(application, actor, reason: str = "") -> None:
    last = (
        ApplicationDecision.objects.filter(
            target_type="program_application", target_id=str(application.pk)
        )
        .exclude(action="revert")
        .order_by("-created_at")
        .first()
    )
    if last is None or last.action not in REVERTABLE_ACTIONS:
        raise ValueError(_("No revertable decision found for this application."))
    old_status = application.status
    application.status = last.old_status
    application.rejection_reason = ""
    application.save(update_fields=["status", "rejection_reason", "updated_at"])
    _record_decision(
        "program_application", application.pk, "revert", old_status, application.status, actor, reason=reason
    )
    record_audit_event(
        actor=actor,
        action=ACTION_APPLICATION_DECISION_REVERTED,
        target_type="program_application",
        target_id=str(application.pk),
        metadata={"old_status": old_status, "new_status": application.status, "reason": reason},
    )
