from __future__ import annotations

from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from .email_config import save_configuration
from .models import EmailConfiguration


class EmailConfigurationForm(forms.ModelForm):
    password = forms.CharField(
        label=_("Password"),
        required=False,
        strip=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text=_("Leave blank to keep the current password."),
    )

    class Meta:
        model = EmailConfiguration
        fields = (
            "name",
            "host",
            "port",
            "username",
            "use_tls",
            "use_ssl",
            "from_email",
            "from_name",
            "timeout",
            "is_active",
        )
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": _("Primary SMTP")}),
            "host": forms.TextInput(attrs={"placeholder": "smtp.example.com"}),
            "from_email": forms.EmailInput(attrs={"placeholder": "no-reply@example.com"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields["from_email"].initial = settings.DEFAULT_FROM_EMAIL
        self.fields["password"].help_text = (
            _("Leave blank to keep the current password.")
            if self.instance.pk and self.instance.has_password
            else _("SMTP password. Leave blank if the server does not require one.")
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("use_tls") and cleaned.get("use_ssl"):
            self.add_error("use_ssl", _("Use either TLS or SSL, not both."))
        timeout = cleaned.get("timeout")
        if timeout is not None and not 1 <= timeout <= 120:
            self.add_error("timeout", _("Timeout must be between 1 and 120 seconds."))
        for field in ("from_name", "from_email"):
            value = cleaned.get(field) or ""
            if "\r" in value or "\n" in value:
                self.add_error(field, _("Line breaks are not allowed in sender fields."))
        return cleaned

    def save(self, commit: bool = True):
        config = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if password:
            config.set_password(password)
        if commit:
            save_configuration(config)
        return config
