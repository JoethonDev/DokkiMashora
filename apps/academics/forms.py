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


class DiplomaForm(forms.ModelForm):
    class Meta:
        model = Diploma
        fields = ["title", "description", "total_semesters", "is_active", "video_tracking_enabled", "video_completion_percent"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
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

    def save(self, commit=True):
        instance = super().save(commit=commit)
        if commit and self.has_changed():
            changed = set(self.changed_data)
            track_fields = changed & {"video_tracking_enabled", "video_completion_percent"}
            if track_fields:
                update = {f: getattr(instance, f) for f in track_fields}
                Course.objects.filter(
                    semester_courses__semester__diploma=instance
                ).update(**update)
        return instance


class SemesterForm(forms.ModelForm):
    class Meta:
        model = Semester
        fields = ["order", "title", "is_active"]

    def __init__(self, *args, **kwargs):
        self.diploma = kwargs.pop("diploma", None)
        super().__init__(*args, **kwargs)

    def clean_order(self):
        order = self.cleaned_data["order"]
        if self.diploma and order > self.diploma.total_semesters:
            raise forms.ValidationError(
                _("Order cannot exceed diploma total of %(max)s semesters.") % {"max": self.diploma.total_semesters}
            )
        return order


class CourseForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = ["title", "description", "is_standalone", "is_active", "video_tracking_enabled", "video_completion_percent"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def clean(self):
        cleaned = super().clean()
        standalone = cleaned.get("is_standalone")
        if standalone and self.instance and self.instance.pk:
            if self.instance.semester_courses.exists():
                raise forms.ValidationError(
                    _("This course is linked to a semester. Unlink it first before marking as standalone.")
                )
        return cleaned


class CohortForm(forms.ModelForm):
    class Meta:
        model = Cohort
        fields = ["name", "start_date", "end_date", "is_active"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }


class CourseVersionForm(forms.ModelForm):
    class Meta:
        model = CourseVersion
        fields = [
            "cohort",
            "semester",
            "label",
            "access_duration_days",
            "is_active",
        ]

class LectureForm(forms.ModelForm):
    audio_file = forms.FileField(required=False, label=_("Audio file (upload)"))

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
            "description": forms.Textarea(attrs={"rows": 3}),
            "release_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
        }


class DiplomaLectureForm(forms.ModelForm):
    audio_file = forms.FileField(required=False, label=_("Audio file (upload)"))

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
            "description": forms.Textarea(attrs={"rows": 3}),
            "release_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
        }
