"""Forms for student profile editing."""

from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.accounts.constants import GROUP_STUDENT, STAFF_ROLE_GROUPS
from apps.accounts.models import User
from apps.core.forms import StyledFormMixin
from apps.students.identity import (
    country_code_field_choices,
    normalize_country_code,
    validate_unique_identity_values,
)
from apps.students.models import StudentProfile

from .policies import EDITABLE_FIELDS


class StudentProfileUpdateForm(StyledFormMixin, forms.ModelForm):
    """Form for editing student-allowed profile fields.

    Only fields listed in EDITABLE_FIELDS are included.  Restricted fields
    are silently ignored even if submitted via crafted POST.
    """

    class Meta:
        model = StudentProfile
        fields = EDITABLE_FIELDS

    def clean_phone(self):
        phone = self.cleaned_data.get("phone", "").strip()
        if not phone:
            raise ValidationError(_("Phone number is required."))
        normalized, _identity = validate_unique_identity_values(
            country_code=self.instance.country_code,
            phone=phone,
            user_id=self.instance.user_id,
            profile_id=self.instance.pk,
        )
        return normalized


class StudentProfileAdminForm(StyledFormMixin, forms.ModelForm):
    country_code = forms.ChoiceField(choices=[], label=_("Country"))

    class Meta:
        model = StudentProfile
        fields = (
            "user", "full_name", "date_of_birth", "gender", "address", "phone",
            "country_code", "national_id", "education", "specialization", "employer",
            "current_job", "church", "priest_name", "priest_contact",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["country_code"].choices = country_code_field_choices(
            self.instance.country_code if self.instance.pk else ""
        )
        if self.instance.pk:
            # Profile ownership determines access to every linked academic and
            # financial record; it is not an editable profile attribute.
            self.fields["user"].queryset = User.objects.filter(pk=self.instance.user_id)
            self.fields["user"].disabled = True
        else:
            self.fields["user"].queryset = (
                User.objects.filter(groups__name=GROUP_STUDENT, student_profile__isnull=True)
                .filter(is_superuser=False)
                .exclude(groups__name__in=STAFF_ROLE_GROUPS)
                .distinct()
                .order_by("email")
            )

    def clean_country_code(self):
        value = (self.cleaned_data.get("country_code") or "").strip().upper()
        return normalize_country_code(value)

    def clean_phone(self):
        raw_phone = (self.cleaned_data.get("phone") or "").strip()
        user = self.cleaned_data.get("user") or getattr(self.instance, "user", None)
        normalized_phone, _identity = validate_unique_identity_values(
            country_code=self.cleaned_data.get("country_code") or self.instance.country_code,
            phone=raw_phone,
            user_id=user.pk if user else None,
            profile_id=self.instance.pk,
        )
        return normalized_phone

    def clean_national_id(self):
        raw_identity = (self.cleaned_data.get("national_id") or "").strip()
        user = self.cleaned_data.get("user") or getattr(self.instance, "user", None)
        _phone, normalized_identity = validate_unique_identity_values(
            country_code=self.cleaned_data.get("country_code") or self.instance.country_code,
            identity_number=raw_identity,
            user_id=user.pk if user else None,
            profile_id=self.instance.pk,
        )
        return normalized_identity
