"""Application business logic: duplicate checking, phone normalization, submission."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction

from apps.accounts.constants import GROUP_STUDENT
from apps.students.models import StudentProfile

from .constants import APP_STATUS_PENDING
from .models import Application

User = get_user_model()

# Country code prefixes that get stripped from a phone number
# (in addition to any leading +, 00, or 0).
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
            digits = digits[len(code) :]
            break
    if digits.startswith("0"):
        digits = digits[1:]
    return digits


def find_duplicate(data: RegistrationData) -> str | None:
    """Return the field name that conflicts, or None when the data is fresh.

    Check order per guardrails: national ID → email → phone.
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


def open_application_for(student: StudentProfile) -> Application:
    """Return the active application, reopening a rejected one if needed."""
    application = student.applications.exclude(status="rejected").order_by("-created_at").first()
    if application is not None:
        return application
    return Application.objects.create(student=student, status=APP_STATUS_PENDING)


@transaction.atomic
def submit_registration(data: RegistrationData) -> tuple[StudentProfile, Application]:
    """Create a user, profile, and pending application.

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
    application = Application.objects.create(student=profile, status=APP_STATUS_PENDING)
    return profile, application


def _student_group():
    group, _ = Group.objects.get_or_create(name=GROUP_STUDENT)
    return group
