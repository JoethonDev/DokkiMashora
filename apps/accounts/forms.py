"""Authentication and profile forms."""

from __future__ import annotations

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserChangeForm, UserCreationForm
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
