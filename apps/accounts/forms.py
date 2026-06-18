"""Authentication and profile forms."""

from __future__ import annotations

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserChangeForm, UserCreationForm
from django.utils.translation import gettext_lazy as _

from .models import User


class StyledMixin:
    """Adds shared CSS classes to all rendered form widgets."""

    base_class = (
        "w-full rounded-lg border border-slate-300 dark:border-slate-700 "
        "bg-white dark:bg-slate-900 px-3 py-2 text-sm shadow-sm "
        "focus:border-brand-500 focus:outline-none focus:ring-2 "
        "focus:ring-brand-200 dark:focus:ring-brand-900 dark:text-slate-100"
    )

    def apply_styling(self) -> None:
        for _name, field in self.fields.items():
            widget = field.widget
            css = widget.attrs.get("class", "")
            if isinstance(widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                widget.attrs["class"] = (
                    css + " h-4 w-4 rounded text-brand-600 focus:ring-brand-500"
                ).strip()
            elif isinstance(widget, forms.Select):
                widget.attrs["class"] = (css + " " + self.base_class).strip()
            else:
                widget.attrs["class"] = (css + " " + self.base_class).strip()


class EmailAuthenticationForm(StyledMixin, AuthenticationForm):
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

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.apply_styling()


class ProfileForm(StyledMixin, forms.ModelForm):
    """Allow the user to update their own basic profile fields."""

    class Meta:
        model = User
        fields = ("full_name", "full_name_ar", "phone")
        labels = {
            "full_name": _("Full name"),
            "full_name_ar": _("Full name (Arabic)"),
            "phone": _("Phone number"),
        }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.apply_styling()


class AdminUserCreationForm(StyledMixin, UserCreationForm):
    class Meta:
        model = User
        fields = ("email",)
        labels = {"email": _("Email")}

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.apply_styling()


class AdminUserChangeForm(StyledMixin, UserChangeForm):
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

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.apply_styling()
