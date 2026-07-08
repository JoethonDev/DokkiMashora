from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from .constants import (
    FIELD_TYPE_BOOLEAN,
    FIELD_TYPE_CHOICE,
    FIELD_TYPE_CHOICES,
    FIELD_TYPE_DATE,
    FIELD_TYPE_NUMBER,
)


class IntakeFormBuildForm(forms.Form):
    title = forms.CharField(max_length=255, label=_("Title"))
    description = forms.CharField(
        widget=forms.Textarea, required=False, label=_("Description"),
    )
    target_type = forms.ChoiceField(
        choices=[("course_version", _("Course Version")), ("cohort", _("Cohort"))],
        label=_("Target type"),
    )
    target_id = forms.IntegerField(label=_("Target ID"))
    is_required = forms.BooleanField(required=False, label=_("Required"))
    is_active = forms.BooleanField(required=False, label=_("Active"))
    open_at = forms.DateTimeField(
        required=False, label=_("Open at"),
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
    close_at = forms.DateTimeField(
        required=False, label=_("Close at"),
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )


class IntakeFormFieldForm(forms.Form):
    label = forms.CharField(max_length=255, label=_("Label"))
    help_text = forms.CharField(max_length=500, required=False, label=_("Help text"))
    field_type = forms.ChoiceField(choices=FIELD_TYPE_CHOICES, label=_("Field type"))
    required = forms.BooleanField(required=False, label=_("Required"))
    options = forms.CharField(
        widget=forms.Textarea, required=False, label=_("Options (one per line)"),
        help_text=_("Only for choice fields"),
    )
    order = forms.IntegerField(label=_("Order"))


class IntakeFormSubmitForm(forms.Form):
    """Dynamic intake form submission validated server-side."""

    def __init__(self, form_instance, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.form_instance = form_instance
        for field in form_instance.fields.filter(is_active=True).order_by("order"):
            key = f"field_{field.pk}"
            if field.field_type == FIELD_TYPE_BOOLEAN:
                self.fields[key] = forms.ChoiceField(
                    choices=[("true", _("Yes")), ("false", _("No"))],
                    required=field.required, label=field.label,
                    help_text=field.help_text,
                    widget=forms.RadioSelect,
                )
            elif field.field_type == FIELD_TYPE_NUMBER:
                self.fields[key] = forms.FloatField(
                    required=field.required, label=field.label,
                    help_text=field.help_text,
                )
            elif field.field_type == FIELD_TYPE_DATE:
                self.fields[key] = forms.DateField(
                    required=field.required, label=field.label,
                    help_text=field.help_text,
                    widget=forms.DateInput(attrs={"type": "date"}),
                )
            elif field.field_type == FIELD_TYPE_CHOICE:
                choices = [(o, o) for o in field.options]
                self.fields[key] = forms.ChoiceField(
                    required=field.required, label=field.label,
                    choices=choices, help_text=field.help_text,
                )
            else:
                self.fields[key] = forms.CharField(
                    required=field.required, label=field.label,
                    help_text=field.help_text,
                    max_length=500,
                )
