from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms import StyledFormMixin


class AttendancePeriodWindowForm(StyledFormMixin, forms.Form):
    starts_at = forms.DateTimeField(
        label=_("Period starts"),
        input_formats=("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"),
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "dir": "ltr"},
            format="%Y-%m-%dT%H:%M",
        ),
    )
    ends_at = forms.DateTimeField(
        label=_("Period ends"),
        input_formats=("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"),
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "dir": "ltr"},
            format="%Y-%m-%dT%H:%M",
        ),
    )

    def clean(self):
        cleaned_data = super().clean()
        starts_at = cleaned_data.get("starts_at")
        ends_at = cleaned_data.get("ends_at")
        if starts_at and ends_at and starts_at >= ends_at:
            self.add_error("ends_at", _("The attendance period must start before it ends."))
        return cleaned_data


class AttendanceSessionWindowForm(StyledFormMixin, forms.Form):
    session_date = forms.DateField(
        label=_("Session date"),
        input_formats=("%Y-%m-%d",),
        widget=forms.DateInput(
            attrs={"type": "date", "dir": "ltr"},
            format="%Y-%m-%d",
        ),
    )
    open_at = forms.DateTimeField(
        label=_("Session opens at"),
        input_formats=("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"),
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "dir": "ltr"},
            format="%Y-%m-%dT%H:%M",
        ),
    )
    close_at = forms.DateTimeField(
        label=_("Session closes at"),
        input_formats=("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"),
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "dir": "ltr"},
            format="%Y-%m-%dT%H:%M",
        ),
    )
    title = forms.CharField(
        label=_("Title"),
        required=False,
        max_length=255,
    )

    def clean(self):
        cleaned_data = super().clean()
        open_at = cleaned_data.get("open_at")
        close_at = cleaned_data.get("close_at")
        if open_at and close_at and open_at >= close_at:
            self.add_error("close_at", _("Closing time must be after opening time."))
        return cleaned_data
