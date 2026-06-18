"""Forms for the public registration flow and admin review actions."""

from __future__ import annotations

from datetime import date

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.accounts.forms import StyledMixin
from apps.applications.models import Application
from apps.applications.services import RegistrationData, find_duplicate, normalize_phone
from apps.students.constants import EDUCATION_CHOICES, GENDER_CHOICES


class RegistrationForm(StyledMixin, forms.Form):
    """Public registration form with all 15 required fields."""

    full_name = forms.CharField(label=_("Full name (4 parts)"), max_length=200)
    date_of_birth = forms.DateField(
        label=_("Date of birth"),
        widget=forms.DateInput(attrs={"type": "date", "max": date.today().isoformat()}),
    )
    gender = forms.ChoiceField(label=_("Gender"), choices=GENDER_CHOICES)
    address = forms.CharField(label=_("Address"), max_length=255)
    phone = forms.CharField(label=_("Mobile number"), max_length=32)
    email = forms.EmailField(label=_("Email"))
    education = forms.ChoiceField(label=_("Education level"), choices=EDUCATION_CHOICES)
    specialization = forms.CharField(label=_("Specialization"), max_length=120, required=False)
    employer = forms.CharField(label=_("Employer"), max_length=120, required=False)
    current_job = forms.CharField(label=_("Current job"), max_length=120, required=False)
    church = forms.CharField(label=_("Church"), max_length=120, required=False)
    priest_name = forms.CharField(label=_("Priest name"), max_length=120, required=False)
    priest_contact = forms.CharField(label=_("Priest contact"), max_length=120, required=False)
    national_id = forms.CharField(label=_("National ID"), max_length=32)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.apply_styling()

    def clean_date_of_birth(self) -> date:
        value: date = self.cleaned_data["date_of_birth"]
        if value >= date.today():
            raise forms.ValidationError(_("Date of birth must be in the past."))
        return value

    def clean_phone(self) -> str:
        value = self.cleaned_data["phone"].strip()
        if not value:
            raise forms.ValidationError(_("Mobile number is required."))
        normalized = normalize_phone(value)
        if len(normalized) < 7:
            raise forms.ValidationError(_("Enter a valid mobile number."))
        return value

    def clean_national_id(self) -> str:
        value = self.cleaned_data["national_id"].strip()
        if not value:
            raise forms.ValidationError(_("National ID is required."))
        return value

    def clean(self):
        cleaned = super().clean()
        required = (
            "email",
            "phone",
            "national_id",
            "full_name",
            "date_of_birth",
            "gender",
            "address",
            "education",
        )
        if all(cleaned.get(field) for field in required):
            data = RegistrationData(
                email=cleaned["email"],
                full_name=cleaned["full_name"],
                date_of_birth=cleaned["date_of_birth"],
                gender=cleaned["gender"],
                address=cleaned["address"],
                phone=cleaned["phone"],
                national_id=cleaned["national_id"],
                education=cleaned["education"],
                specialization=cleaned.get("specialization", ""),
                employer=cleaned.get("employer", ""),
                current_job=cleaned.get("current_job", ""),
                church=cleaned.get("church", ""),
                priest_name=cleaned.get("priest_name", ""),
                priest_contact=cleaned.get("priest_contact", ""),
                age=0,
            )
            duplicate_on = find_duplicate(data)
            if duplicate_on:
                messages = {
                    "email": _("An account with this email already exists."),
                    "phone": _("An account with this mobile number already exists."),
                    "national_id": _("An account with this national ID already exists."),
                    "full_name": _("An account with this full name already exists."),
                }
                self.add_error(duplicate_on, messages[duplicate_on])
        return cleaned


class ApplicationDecisionForm(StyledMixin, forms.ModelForm):
    """Admin form to capture review notes and a rejection reason."""

    class Meta:
        model = Application
        fields = ("admin_notes", "rejection_reason")
        labels = {
            "admin_notes": _("Admin notes"),
            "rejection_reason": _("Rejection reason"),
        }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.apply_styling()
