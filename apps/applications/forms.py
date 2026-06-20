"""Forms for the public registration flow and admin review actions."""

from __future__ import annotations

from datetime import date

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.applications.models import Application
from apps.applications.services import RegistrationData, find_duplicate, normalize_phone
from apps.students.constants import EDUCATION_CHOICES, GENDER_CHOICES


class RegistrationForm(forms.Form):
    """Public registration form with mandatory personal fields and optional program selection."""

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

    # Optional program selection
    apply_program = forms.BooleanField(
        label=_("I want to apply for a program"),
        required=False,
        initial=False,
    )
    program_type = forms.ChoiceField(
        label=_("Program type"),
        choices=[],
        required=False,
        widget=forms.RadioSelect,
    )
    program_target = forms.ChoiceField(
        label=_("Program"),
        choices=[],
        required=False,
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._load_program_choices()

    def _load_program_choices(self) -> None:
        from apps.academics.models import Cohort, Course
        from apps.applications.constants import PROGRAM_TYPE_CHOICES

        self.fields["program_type"].choices = PROGRAM_TYPE_CHOICES
        cohorts = Cohort.objects.filter(is_active=True).select_related("diploma").order_by("-start_date")
        courses = Course.objects.filter(is_active=True).order_by("title")
        cohort_choices = [(f"cohort_{c.pk}", f"{c.diploma.title} - {c.name}") for c in cohorts]
        course_choices = [(f"course_{c.pk}", c.title) for c in courses]
        self._program_target_choices = {
            "diploma": cohort_choices,
            "course": course_choices,
        }

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
                }
                self.add_error(duplicate_on, messages[duplicate_on])

            apply_program = cleaned.get("apply_program")
            program_type = cleaned.get("program_type")
            program_target = cleaned.get("program_target")
            if apply_program:
                if not program_type:
                    self.add_error("program_type", _("Please select a program type."))
                if not program_target:
                    self.add_error("program_target", _("Please select a program."))
                if program_type and program_target:
                    if program_type == "diploma" and not program_target.startswith("cohort_"):
                        self.add_error("program_target", _("Invalid diploma selection."))
                    if program_type == "course" and not program_target.startswith("course_"):
                        self.add_error("program_target", _("Invalid course selection."))

        return cleaned

    def get_program_data(self) -> dict:
        """Extract optional program selection from cleaned data."""
        result = {"program_type": None, "cohort_id": None, "course_id": None}
        if not self.cleaned_data.get("apply_program"):
            return result
        program_type = self.cleaned_data.get("program_type")
        program_target = self.cleaned_data.get("program_target")
        if program_type == "diploma" and program_target and program_target.startswith("cohort_"):
            result["program_type"] = "diploma"
            result["cohort_id"] = int(program_target.split("_", 1)[1])
        elif program_type == "course" and program_target and program_target.startswith("course_"):
            result["program_type"] = "course"
            result["course_id"] = int(program_target.split("_", 1)[1])
        return result


class ApplicationDecisionForm(forms.ModelForm):
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


class ProgramApplicationDecisionForm(forms.ModelForm):
    """Admin form for ProgramApplication review actions."""

    class Meta:
        model = Application
        fields = ("admin_notes", "rejection_reason")
        labels = {
            "admin_notes": _("Admin notes"),
            "rejection_reason": _("Rejection reason"),
        }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
