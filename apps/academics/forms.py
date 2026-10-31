"""Forms for academic structure management."""

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.academics.models import (
    Cohort,
    Course,
    CourseVersion,
    Diploma,
    DiplomaLecture,
    Lecture,
    Semester,
)
from apps.core.forms import StyledFormMixin


class DiplomaForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Diploma
        fields = ["title", "description", "total_semesters", "is_active", "attendance_enabled", "video_tracking_enabled", "video_completion_percent"]
        widgets = {
            "title": forms.TextInput(attrs={}),
            "description": forms.Textarea(attrs={"rows": 4}),
            "total_semesters": forms.NumberInput(attrs={}),
            "is_active": forms.CheckboxInput(attrs={}),
            "attendance_enabled": forms.CheckboxInput(attrs={}),
            "video_tracking_enabled": forms.CheckboxInput(attrs={}),
            "video_completion_percent": forms.NumberInput(attrs={}),
        }

    def clean_total_semesters(self):
        total = self.cleaned_data["total_semesters"]
        if self.instance and self.instance.pk:
            existing = self.instance.semesters.count()
            if total < existing:
                raise forms.ValidationError(
                    _("Cannot reduce below %(count)s existing semester(s).") % {"count": existing}
                )
        return total

class SemesterForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Semester
        fields = ["order", "title", "is_active"]
        widgets = {
            "order": forms.NumberInput(attrs={}),
            "title": forms.TextInput(attrs={}),
            "is_active": forms.CheckboxInput(attrs={}),
        }

    def __init__(self, *args, **kwargs):
        self.diploma = kwargs.pop("diploma", None)
        super().__init__(*args, **kwargs)

    def clean_order(self):
        order = self.cleaned_data["order"]
        if self.diploma and order > self.diploma.total_semesters:
            raise forms.ValidationError(
                _("Order cannot exceed diploma total of %(max)s semesters.") % {"max": self.diploma.total_semesters}
            )
        if self.diploma:
            existing = Semester.objects.filter(diploma=self.diploma, order=order)
            if self.instance.pk:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                raise forms.ValidationError(_("A semester with this order already exists for the diploma."))
        return order


class CourseForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Course
        fields = ["title", "description", "is_active", "attendance_enabled", "video_tracking_enabled", "video_completion_percent"]
        widgets = {
            "title": forms.TextInput(attrs={}),
            "description": forms.Textarea(attrs={"rows": 4}),
            "is_active": forms.CheckboxInput(attrs={}),
            "attendance_enabled": forms.CheckboxInput(attrs={}),
            "video_tracking_enabled": forms.CheckboxInput(attrs={}),
            "video_completion_percent": forms.NumberInput(attrs={}),
        }

    def save(self, commit=True):
        course = super().save(commit=False)
        if commit:
            course.save()
            self.save_m2m()
        return course


class CohortForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Cohort
        fields = ["name", "start_date", "end_date", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={}),
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        if start_date and end_date and end_date < start_date:
            self.add_error("end_date", _("End date must be on or after the start date."))
        return cleaned_data


class CourseVersionForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = CourseVersion
        fields = [
            "label",
            "access_duration_days",
            "is_active",
        ]
        widgets = {
            "label": forms.TextInput(attrs={}),
            "access_duration_days": forms.NumberInput(attrs={}),
            "is_active": forms.CheckboxInput(attrs={}),
        }

    def __init__(self, *args, **kwargs):
        self.course = kwargs.pop("course", None)
        super().__init__(*args, **kwargs)
        if self.course is not None:
            self.instance.course = self.course

    def clean_label(self):
        label = (self.cleaned_data.get("label") or "").strip()
        if not label:
            label = CourseVersion._meta.get_field("label").default
        if self.course is not None:
            duplicates = CourseVersion.objects.filter(course=self.course, label=label)
            if self.instance.pk:
                duplicates = duplicates.exclude(pk=self.instance.pk)
            if duplicates.exists():
                raise forms.ValidationError(
                    _("A version with this label already exists for this course.")
                )
        return label

class LectureForm(StyledFormMixin, forms.ModelForm):
    audio_file = forms.FileField(required=False, label=_("Audio file (upload)"), widget=forms.FileInput(attrs={}))

    class Meta:
        model = Lecture
        fields = [
            "order",
            "title",
            "description",
            "youtube_url",
            "audio_url",
            "release_at",
            "is_published",
        ]
        widgets = {
            "order": forms.NumberInput(attrs={}),
            "title": forms.TextInput(attrs={}),
            "description": forms.Textarea(attrs={"rows": 3}),
            "youtube_url": forms.URLInput(attrs={}),
            "audio_url": forms.URLInput(attrs={}),
            "release_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
        }


class DiplomaLectureForm(StyledFormMixin, forms.ModelForm):
    audio_file = forms.FileField(required=False, label=_("Audio file (upload)"), widget=forms.FileInput(attrs={}))

    class Meta:
        model = DiplomaLecture
        fields = [
            "order",
            "title",
            "description",
            "youtube_url",
            "audio_url",
            "release_at",
            "is_published",
        ]
        widgets = {
            "order": forms.NumberInput(attrs={}),
            "title": forms.TextInput(attrs={}),
            "description": forms.Textarea(attrs={"rows": 3}),
            "youtube_url": forms.URLInput(attrs={}),
            "audio_url": forms.URLInput(attrs={}),
            "release_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
        }
