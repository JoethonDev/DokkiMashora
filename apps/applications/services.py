"""Application business logic: duplicate matching, submission, status transitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from django.contrib.auth import get_user_model
from django.db import transaction

from apps.accounts.constants import GROUP_STUDENT
from apps.students.models import StudentProfile

from .constants import (
    APP_STATUS_PENDING,
    APP_STATUS_REJECTED,
)
from .models import Application

User = get_user_model()


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


def find_existing_user(*, email: str, national_id: str, phone: str) -> tuple[Any | None, str | None]:
    """Return (user, match_field) for the first identifier that hits."""
    if email:
        user = User.objects.filter(email__iexact=email).first()
        if user is not None:
            return user, "email"
    if national_id:
        profile = StudentProfile.objects.filter(national_id=national_id).select_related("user").first()
        if profile is not None:
            return profile.user, "national_id"
    if phone:
        profile = StudentProfile.objects.filter(phone=phone).select_related("user").first()
        if profile is not None:
            return profile.user, "phone"
    return None, None


def open_application_for(student: StudentProfile) -> Application:
    """Return the active application, reopening a rejected one if needed."""
    application = student.applications.exclude(status=APP_STATUS_REJECTED).order_by("-created_at").first()
    if application is not None:
        return application
    return Application.objects.create(student=student, status=APP_STATUS_PENDING)


@transaction.atomic
def submit_registration(data: RegistrationData) -> tuple[StudentProfile, Application, bool]:
    """Create or reuse a user + student profile, then create a pending application.

    Returns (student_profile, application, created_user).
    """
    user, match_field = find_existing_user(email=data.email, national_id=data.national_id, phone=data.phone)
    if user is None:
        user = User.objects.create_user(
            email=data.email,
            password=None,
            full_name=data.full_name,
            phone=data.phone,
            national_id=data.national_id,
        )
        user.set_unusable_password()
        user.save(update_fields=["password"])
        user.groups.add(_student_group())
        created_user = True
    else:
        # Person matched on national_id or phone: refresh contact info
        # and email from the new submission.
        update_fields = ["full_name", "phone", "national_id", "updated_at"]
        user.full_name = data.full_name
        user.phone = data.phone
        user.national_id = data.national_id
        if match_field in {"national_id", "phone"} and user.email != data.email:
            user.email = data.email
            update_fields.append("email")
        user.save(update_fields=update_fields)
        created_user = False

    profile, _ = StudentProfile.objects.update_or_create(
        user=user,
        defaults={
            "full_name": data.full_name,
            "date_of_birth": data.date_of_birth,
            "gender": data.gender,
            "address": data.address,
            "phone": data.phone,
            "national_id": data.national_id,
            "education": data.education,
            "specialization": data.specialization,
            "employer": data.employer,
            "current_job": data.current_job,
            "church": data.church,
            "priest_name": data.priest_name,
            "priest_contact": data.priest_contact,
        },
    )
    application = open_application_for(profile)
    if match_field and created_user is False:
        return profile, application, False
    return profile, application, created_user


def _student_group():
    from django.contrib.auth.models import Group

    group, _ = Group.objects.get_or_create(name=GROUP_STUDENT)
    return group
