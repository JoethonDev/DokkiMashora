"""Student ownership and edit-field policies."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.utils.translation import gettext_lazy as _

if TYPE_CHECKING:
    from apps.students.models import StudentProfile


EDITABLE_FIELDS = [
    "phone",
    "address",
]

RESTRICTED_FIELDS = [
    "full_name",
    "national_id",
    "email",
    "date_of_birth",
    "gender",
    "education",
    "specialization",
    "employer",
    "current_job",
    "church",
    "priest_name",
    "priest_contact",
]


def get_editable_fields() -> list[str]:
    """Return the list of field names a student may edit themselves."""
    return list(EDITABLE_FIELDS)


def get_restricted_fields() -> list[str]:
    """Return the list of field names a student may NOT edit."""
    return list(RESTRICTED_FIELDS)


def can_edit_field(field_name: str) -> bool:
    """Check whether a given field name is student-editable."""
    return field_name in EDITABLE_FIELDS


def get_field_label(field_name: str) -> str:
    """Return a translatable user-facing label for a profile field."""
    labels = {
        "full_name": _("Full name"),
        "email": _("Email"),
        "phone": _("Phone number"),
        "national_id": _("National ID"),
        "date_of_birth": _("Date of birth"),
        "gender": _("Gender"),
        "address": _("Address"),
        "education": _("Education level"),
        "specialization": _("Specialization"),
        "employer": _("Employer"),
        "current_job": _("Current job"),
        "church": _("Church"),
        "priest_name": _("Priest name"),
        "priest_contact": _("Priest contact"),
    }
    return labels.get(field_name, field_name)


def is_own_profile(profile: StudentProfile, user) -> bool:
    """Check whether a User owns the given StudentProfile."""
    return profile.user_id == user.id
