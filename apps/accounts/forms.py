"""Authentication and profile forms."""

from __future__ import annotations

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserChangeForm, UserCreationForm
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .models import User


class EmailAuthenticationForm(AuthenticationForm):
    """Authenticate using email rather than username."""

    username = forms.EmailField(
        label=_("Email"),
        widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email"}),
    )
    password = forms.CharField(
        label=_("Password"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )


class ProfileForm(forms.ModelForm):
    """Allow the user to update their own basic profile fields."""

    class Meta:
        model = User
        fields = ("full_name", "full_name_ar", "phone")
        labels = {
            "full_name": _("Full name"),
            "full_name_ar": _("Full name (Arabic)"),
            "phone": _("Phone number"),
        }


class AdminUserCreationForm(UserCreationForm):
    class Meta:
        model = User
        fields = ("email",)
        labels = {"email": _("Email")}


class AdminUserChangeForm(UserChangeForm):
    class Meta:
        model = User
        fields = (
            "email",
            "full_name",
            "full_name_ar",
            "phone",
            "national_id",
            "status",
            "is_active",
            "is_staff",
        )


class UserEditForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["full_name", "full_name_ar", "phone", "national_id", "status", "is_active"]


class UserUploadForm(forms.Form):
    csv_file = forms.FileField(
        label=_("CSV file"),
        help_text=_("Upload a CSV file with columns: email, full_name"),
        widget=forms.FileInput(attrs={"accept": ".csv"}),
    )

    def clean_csv_file(self):
        file = self.cleaned_data.get("csv_file")
        if not file:
            raise ValidationError(_("No file provided"))
        if not file.name.endswith(".csv"):
            raise ValidationError(_("Only CSV files are accepted"))
        if file.size > 10 * 1024 * 1024:
            raise ValidationError(
                _("File size exceeds the maximum of 10 MB")
            )
        if file.size == 0:
            raise ValidationError(_("Uploaded file is empty"))
        return file
