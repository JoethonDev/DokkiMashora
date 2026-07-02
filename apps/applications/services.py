"""Application business logic: duplicate checking, phone normalization, submission."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.academics.models import Cohort, Course
from apps.accounts.constants import GROUP_STUDENT
from apps.accounts.constants import USER_STATUS_INACTIVE
from apps.audit.constants import ACTION_APPLICATION_DECISION_REVERTED
from apps.audit.services import record_audit_event
from apps.communications.constants import (
    TEMPLATE_ACCEPTANCE,
    TEMPLATE_ACTIVATION,
    TEMPLATE_REJECTION,
)
from apps.communications.services import send_templated_email
from apps.students.models import StudentProfile

from .constants import (
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_REJECTED,
    PROGRAM_APP_STATUS_SUBMITTED,
    PROGRAM_TYPE_COURSE,
    PROGRAM_TYPE_DIPLOMA,
)
from .models import ProgramApplication, ProgramApplicationDecision, ProgramApplicationEmailLog

if TYPE_CHECKING:
    from apps.accounts.models import User as UserModel

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
    """Return the field name that conflicts, or None when the data is fresh."""
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
) -> tuple[StudentProfile, ProgramApplication | None]:
    """Create a user, profile, and optional program application.

    Returns (StudentProfile, ProgramApplication | None).
    Raises ValueError with a duplicate field name if the data conflicts.
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
        national_id=data.national_id,
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
# Decision services
# ---------------------------------------------------------------------------


def accept_program_application(application, actor, notes: str = "", send_email: bool = False) -> None:
    with transaction.atomic():
        old_status = application.status
        application.accept(actor, notes=notes)
        _record_decision(application, "accept", old_status, application.status, actor)
        record_audit_event(
            actor=actor,
            action="application.approved",
            target_type="program_application",
            target_id=str(application.pk),
            metadata={"student_id": application.student_id, "send_email": send_email},
        )
        if send_email:
            transaction.on_commit(lambda: _send_program_app_acceptance_email(application))


def reject_program_application(application, actor, reason: str, notes: str = "", send_email: bool = False) -> None:
    with transaction.atomic():
        old_status = application.status
        application.reject(actor, reason=reason, notes=notes)
        _record_decision(application, "reject", old_status, application.status, actor, reason=reason)
        record_audit_event(
            actor=actor,
            action="application.rejected",
            target_type="program_application",
            target_id=str(application.pk),
            metadata={"reason": reason, "student_id": application.student_id, "send_email": send_email},
        )
        if send_email:
            transaction.on_commit(lambda: _send_program_app_rejection_email(application, reason))


def revert_program_application_decision(application, actor, reason: str = "") -> None:
    if application.entrance_attempts.exists():
        raise ValueError(_("You cannot revert an application after an entrance exam attempt exists."))

    last_decision = application.decisions.exclude(action="revert").order_by("-created_at").first()
    if not last_decision:
        raise ValueError(_("No decision to revert."))

    with transaction.atomic():
        old_status = application.status
        application.status = last_decision.old_status
        application.reviewed_by = actor
        application.reviewed_at = timezone.now()
        application.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
        _record_decision(application, "revert", old_status, application.status, actor, reason=reason)
        record_audit_event(
            actor=actor,
            action=ACTION_APPLICATION_DECISION_REVERTED,
            target_type="program_application",
            target_id=str(application.pk),
            metadata={"student_id": application.student_id, "reason": reason},
        )


def _record_decision(application, action, old_status, new_status, actor, reason: str = ""):
    return ProgramApplicationDecision.objects.create(
        program_application=application,
        action=action,
        old_status=old_status,
        new_status=new_status,
        actor=actor,
        reason=reason,
    )


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
