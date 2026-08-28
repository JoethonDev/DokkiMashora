"""Forms for the public registration flow and admin review actions."""

from __future__ import annotations

import os
from datetime import date
from typing import Any

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.academics.models import Cohort, Course
from apps.applications.constants import (
    DELIVERY_MODE_CHOICES,
    DELIVERY_MODE_ONLINE,
    PROGRAM_TYPE_CHOICES,
)
from apps.applications.documents import ATTACHMENT_ALLOWED_EXTENSIONS
from apps.applications.services import (
    RegistrationData,
    find_duplicate,
    is_apply_target_open,
)
from apps.core.forms import StyledFormMixin
from apps.core.phone import validate_phone
from apps.students.constants import EDUCATION_CHOICES, GENDER_CHOICES
from apps.students.identity import (
    COUNTRY_CHOICES,
    identity_label,
    normalize_country_code,
    validate_identity_number,
)


class RegistrationForm(StyledFormMixin, forms.Form):
    """Public registration form with mandatory personal fields and optional program selection."""

    full_name = forms.CharField(label=_("Full name (4 parts)"), max_length=200, widget=forms.TextInput(attrs={}))
    date_of_birth = forms.DateField(
        label=_("Date of birth"),
        widget=forms.DateInput(attrs={"type": "date", "max": date.today().isoformat()}),
    )
    gender = forms.ChoiceField(label=_("Gender"), choices=GENDER_CHOICES, widget=forms.Select(attrs={}))
    address = forms.CharField(label=_("Address"), max_length=255, widget=forms.TextInput(attrs={}))
    phone = forms.CharField(label=_("Mobile number"), max_length=32, widget=forms.TextInput(attrs={}))
    email = forms.EmailField(label=_("Email"), widget=forms.EmailInput(attrs={}))
    education = forms.ChoiceField(label=_("Education level"), choices=EDUCATION_CHOICES, widget=forms.Select(attrs={}))
    country_code = forms.ChoiceField(
        label=_("Country"),
        choices=COUNTRY_CHOICES,
        initial="EG",
        widget=forms.Select(attrs={}),
    )
    national_id = forms.CharField(label=_("National ID"), max_length=32, widget=forms.TextInput(attrs={}))
    specialization = forms.CharField(label=_("Specialization"), max_length=120, widget=forms.TextInput(attrs={}))
    employer = forms.CharField(label=_("Employer"), max_length=120, widget=forms.TextInput(attrs={}))
    current_job = forms.CharField(label=_("Current job"), max_length=120, widget=forms.TextInput(attrs={}))
    church = forms.CharField(label=_("Church"), max_length=120, widget=forms.TextInput(attrs={}))
    priest_name = forms.CharField(label=_("Priest name"), max_length=120, widget=forms.TextInput(attrs={}))
    priest_contact = forms.CharField(label=_("Priest contact"), max_length=120, widget=forms.TextInput(attrs={}))

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
        widget=forms.Select,
    )
    diploma = forms.ChoiceField(
        label=_("Diploma"),
        choices=[],
        required=False,
        widget=forms.Select(attrs={}),
    )
    delivery_mode = forms.ChoiceField(
        label=_("Diploma attendance mode"),
        choices=DELIVERY_MODE_CHOICES,
        required=False,
        initial=DELIVERY_MODE_ONLINE,
        widget=forms.Select(attrs={}),
    )
    program_target = forms.CharField(
        label=_("Program"),
        required=False,
        widget=forms.TextInput(attrs={}),
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._load_program_choices()
        cc_raw = (self.data.get("country_code") if self.data else None) or self.initial.get("country_code") or "EG"
        country_code = normalize_country_code(cc_raw)
        self.fields["national_id"].label = identity_label(country_code)
        self.fields["national_id"].help_text = (
            _("14 digits · e.g., 29001010100001")
            if country_code == "EG" else
            _("e.g., A1234567")
        )
        phone_prefix = {"EG": "+20", "SA": "+966", "AE": "+971", "US": "+1", "GB": "+44", "CA": "+1", "AU": "+61"}.get(country_code, "+country code")
        self.fields["phone"].help_text = _(
            "e.g., %(prefix)s 10 1234 5678"
        ) % {"prefix": phone_prefix}

    def _load_program_choices(self) -> None:
        self.fields["program_type"].choices = PROGRAM_TYPE_CHOICES
        today = timezone.now().date()
        cohorts = Cohort.objects.filter(
            is_active=True,
            entrance_exams__is_active=True,
            end_date__isnull=False,
            end_date__gte=today,
        ).select_related("diploma").order_by("-start_date")
        self.fields["diploma"].choices = [("", _("Select a diploma"))] + [
            (str(c.pk), f"{c.diploma.title} — {c.name}") for c in cohorts
        ]
        courses = (
            Course.objects.filter(is_active=True, versions__is_active=True)
            .distinct()
            .order_by("title")
        )
        self.fields["program_target"].widget = forms.Select(
            choices=[("", _("Select a course"))]
            + [(f"course_{course.pk}", course.title) for course in courses]
        )

    def clean_date_of_birth(self) -> date:
        value: date = self.cleaned_data["date_of_birth"]
        if value >= date.today():
            raise forms.ValidationError(_("Date of birth must be in the past."))
        return value

    def clean_phone(self) -> str:
        value = self.cleaned_data["phone"].strip()
        if not value:
            raise forms.ValidationError(_("Mobile number is required."))
        return validate_phone(value)

    def clean_country_code(self) -> str:
        cc = self.cleaned_data.get("country_code", "")
        return normalize_country_code(cc)

    def clean_national_id(self) -> str:
        value = self.cleaned_data.get("national_id", "").strip()
        cc_raw = self.cleaned_data.get("country_code", "")
        country_code = normalize_country_code(cc_raw or "EG")
        label = identity_label(country_code)
        if not value:
            raise forms.ValidationError(
                _("%(label)s is required.") % {"label": label}
            )
        try:
            return validate_identity_number(country_code, value)
        except ValidationError as exc:
            raise forms.ValidationError(str(exc)) from exc

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
            "specialization",
            "employer",
            "current_job",
            "church",
            "priest_name",
            "priest_contact",
        )
        if all(cleaned.get(field) for field in required):
            country_code = normalize_country_code(
                cleaned.get("country_code", "") or "EG"
            )
            data = RegistrationData(
                email=cleaned["email"],
                full_name=cleaned["full_name"],
                date_of_birth=cleaned["date_of_birth"],
                gender=cleaned["gender"],
                address=cleaned["address"],
                phone=cleaned["phone"],
                national_id=cleaned["national_id"],
                country_code=country_code,
                education=cleaned["education"],
                specialization=cleaned.get("specialization", ""),
                employer=cleaned.get("employer", ""),
                current_job=cleaned.get("current_job", ""),
                church=cleaned.get("church", ""),
                priest_name=cleaned.get("priest_name", ""),
                priest_contact=cleaned.get("priest_contact", ""),
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
            diploma = cleaned.get("diploma")
            if apply_program:
                if not program_type:
                    self.add_error("program_type", _("Please select a program type."))
                if program_type == "diploma":
                    if not diploma:
                        self.add_error("diploma", _("Please select a diploma."))
                    if not cleaned.get("delivery_mode"):
                        self.add_error("delivery_mode", _("Please select an attendance mode."))
                    if diploma:
                        cohort = Cohort.objects.filter(pk=diploma).select_related("diploma").first()
                        if not cohort or not is_apply_target_open(cohort=cohort):
                            self.add_error("diploma", _("No active cohort with an entrance exam found for this diploma."))
                        else:
                            self._auto_cohort_id = cohort.pk
                if program_type == "course":
                    if not program_target:
                        self.add_error("program_target", _("Please select a course."))
                    elif not str(program_target).startswith("course_"):
                        self.add_error("program_target", _("Invalid course selection."))
                    else:
                        course_id = str(program_target).split("_", 1)[1]
                        course = Course.objects.filter(pk=course_id).first()
                        if not course or not is_apply_target_open(course=course):
                            self.add_error("program_target", _("Invalid course selection."))

        return cleaned

    def get_program_data(self) -> dict:
        """Extract optional program selection from cleaned data."""
        result: dict[str, Any] = {
            "program_type": None,
            "cohort_id": None,
            "course_id": None,
            "delivery_mode": DELIVERY_MODE_ONLINE,
        }
        if not self.cleaned_data.get("apply_program"):
            return result
        program_type = self.cleaned_data.get("program_type")
        if program_type == "diploma":
            cohort_id = getattr(self, "_auto_cohort_id", None)
            if cohort_id:
                result["program_type"] = "diploma"
                result["cohort_id"] = cohort_id
                result["delivery_mode"] = self.cleaned_data.get("delivery_mode") or DELIVERY_MODE_ONLINE
        elif program_type == "course":
            program_target = self.cleaned_data.get("program_target")
            if program_target and program_target.startswith("course_"):
                result["program_type"] = "course"
                result["course_id"] = int(program_target.split("_", 1)[1])
        return result


class AdminDocumentRequestForm(StyledFormMixin, forms.Form):
    """Admin form to request documents for an accepted diploma application."""

    expires_at = forms.DateTimeField(
        label=_("Expiry date/time"),
        required=True,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )
    notes = forms.CharField(
        label=_("Notes"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    send_email = forms.BooleanField(
        label=_("Send email notification"),
        required=False,
        initial=True,
    )

    def clean_expires_at(self) -> Any:
        value = self.cleaned_data["expires_at"]
        if timezone.is_naive(value):
            value = timezone.make_aware(value)
        if value <= timezone.now():
            raise forms.ValidationError(_("Expiry time must be in the future."))
        return value


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True

    def __init__(self, attrs=None):
        super().__init__(attrs)


class MultipleFileField(forms.FileField):
    def clean(self, data, initial=None):
        if isinstance(data, (list, tuple)):
            return [forms.FileField.clean(self, item, initial) for item in data]
        return [forms.FileField.clean(self, data, initial)]


class StudentDocumentUploadForm(StyledFormMixin, forms.Form):
    """Student upload form for persona image and attachments."""

    persona_image = forms.ImageField(
        label=_("Personal photo"),
        required=True,
        widget=forms.FileInput(attrs={}),
    )
    attachments = MultipleFileField(
        label=_("Documents (one PDF or multiple images)"),
        required=True,
        widget=MultipleFileInput,
    )

    def clean_attachments(self) -> Any:
        files = self.files.getlist("attachments") if self.files else []
        if not files:
            raise forms.ValidationError(_("At least one attachment file is required."))
        allowed = ATTACHMENT_ALLOWED_EXTENSIONS
        has_pdf = False
        has_image = False
        for f in files:
            ext = _get_ext(f.name)
            if ext not in allowed:
                raise forms.ValidationError(
                    _("File type '%(ext)s' is not allowed for attachments.") % {"ext": ext}
                )
            if ext == ".pdf":
                if has_pdf:
                    raise forms.ValidationError(_("Only one PDF file can be uploaded."))
                has_pdf = True
            else:
                has_image = True
            if has_pdf and has_image:
                raise forms.ValidationError(_("Cannot mix PDF and image files in attachments."))
        max_count = settings.APPLICATION_DOCUMENT_MAX_ATTACHMENT_FILES
        if len(files) > max_count:
            raise forms.ValidationError(
                _("Maximum %(max)d attachment files allowed.") % {"max": max_count}
            )
        return files


def _get_ext(name: str) -> str:
    return os.path.splitext(name)[1].lower()
