"""Forms for student profile editing."""

from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.students.models import StudentProfile

from .policies import EDITABLE_FIELDS
from apps.core.forms import StyledFormMixin


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
        qs = StudentProfile.objects.exclude(pk=self.instance.pk).filter(phone=phone)
        if qs.exists():
            raise ValidationError(_("This phone number is already in use."))
        return phone
