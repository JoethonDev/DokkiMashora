"""Application business logic: duplicate checking, phone normalization, submission."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction

from apps.accounts.constants import GROUP_STUDENT
from apps.students.models import StudentProfile

from .constants import (
    APP_STATUS_PENDING,
    PROGRAM_APP_STATUS_SUBMITTED,
    PROGRAM_TYPE_COURSE,
    PROGRAM_TYPE_DIPLOMA,
)
from .models import Application, ProgramApplication

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
        from apps.academics.models import Cohort
        cohort = Cohort.objects.get(pk=data.cohort_id)
        program_application = ProgramApplication.objects.create(
            root_application=root_application,
            student=profile,
            program_type=PROGRAM_TYPE_DIPLOMA,
            cohort=cohort,
            status=PROGRAM_APP_STATUS_SUBMITTED,
        )
    elif data.program_type == PROGRAM_TYPE_COURSE and data.course_id:
        from apps.academics.models import Course
        course = Course.objects.get(pk=data.course_id)
        program_application = ProgramApplication.objects.create(
            root_application=root_application,
            student=profile,
            program_type=PROGRAM_TYPE_COURSE,
            course=course,
            status=PROGRAM_APP_STATUS_SUBMITTED,
        )

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
    from django.utils.translation import gettext_lazy as _

    from apps.academics.models import Cohort as CohortModel
    from apps.academics.models import Course as CourseModel

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
