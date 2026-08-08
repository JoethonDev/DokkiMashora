from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.academics.models import Cohort, CourseVersion

from apps.core.forms import StyledFormMixin

from .constants import (
    FIELD_TYPE_BOOLEAN,
    FIELD_TYPE_CHOICE,
    FIELD_TYPE_CHOICES,
    FIELD_TYPE_DATE,
    FIELD_TYPE_MULTI_CHOICE,
    FIELD_TYPE_NUMBER,
)


class IntakeFormBuildForm(StyledFormMixin, forms.Form):
    title = forms.CharField(max_length=255, label=_("Title"), widget=forms.TextInput(attrs={}))
    description = forms.CharField(
        widget=forms.Textarea(attrs={}), required=False, label=_("Description"),
    )
    target_type = forms.ChoiceField(
        choices=[("course_version", _("Course")), ("cohort", _("Diploma"))],
        label=_("Target type"),
        widget=forms.Select(attrs={}),
    )
    course_version = forms.ModelChoiceField(
        queryset=CourseVersion.objects.filter(is_active=True).select_related("course"),
        required=False, label=_("Course version"),
        widget=forms.Select(attrs={}),
    )
    cohort = forms.ModelChoiceField(
        queryset=Cohort.objects.filter(is_active=True).select_related("diploma"),
        required=False, label=_("Diploma cohort"),
        widget=forms.Select(attrs={}),
    )
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

    def clean(self):
        cleaned = super().clean()
        target_type = cleaned.get("target_type")
        course_version = cleaned.get("course_version")
        cohort = cleaned.get("cohort")
        if target_type == "course_version" and not course_version:
            self.add_error("course_version", _("Select a course version."))
        elif target_type == "cohort" and not cohort:
            self.add_error("cohort", _("Select a diploma cohort."))
        if target_type == "course_version":
            cleaned["cohort"] = None
        elif target_type == "cohort":
            cleaned["course_version"] = None
        return cleaned


class IntakeFormFieldForm(StyledFormMixin, forms.Form):
    label = forms.CharField(max_length=255, label=_("Label"), widget=forms.TextInput(attrs={}))
    help_text = forms.CharField(max_length=500, required=False, label=_("Help text"), widget=forms.TextInput(attrs={}))
    field_type = forms.ChoiceField(choices=FIELD_TYPE_CHOICES, label=_("Field type"), widget=forms.Select(attrs={}))
    required = forms.BooleanField(required=False, label=_("Required"))
    options = forms.CharField(
        widget=forms.Textarea(attrs={}), required=False, label=_("Options (one per line)"),
        help_text=_("Only for choice fields"),
    )
    order = forms.IntegerField(label=_("Order"), widget=forms.NumberInput(attrs={}))


class IntakeFormSubmitForm(StyledFormMixin, forms.Form):
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
                    widget=forms.NumberInput(attrs={}),
                )
            elif field.field_type == FIELD_TYPE_DATE:
                self.fields[key] = forms.DateField(
                    required=field.required, label=field.label,
                    help_text=field.help_text,
                    widget=forms.DateInput(attrs={"type": "date"}),
                )
            elif field.field_type == FIELD_TYPE_CHOICE:
                # ponytail: normalize option values to strings to avoid type mismatch on submission
                choices = [(str(o), str(o)) for o in field.options]
                self.fields[key] = forms.ChoiceField(
                    required=field.required, label=field.label,
                    choices=choices, help_text=field.help_text,
                    widget=forms.Select(attrs={}),
                )
            elif field.field_type == FIELD_TYPE_MULTI_CHOICE:
                choices = [(str(o), str(o)) for o in field.options]
                self.fields[key] = forms.MultipleChoiceField(
                    required=field.required, label=field.label,
                    choices=choices, help_text=field.help_text,
                    widget=forms.CheckboxSelectMultiple,
                )
            else:
                self.fields[key] = forms.CharField(
                    required=field.required, label=field.label,
                    help_text=field.help_text,
                    max_length=500,
                    widget=forms.TextInput(attrs={}),
                )
