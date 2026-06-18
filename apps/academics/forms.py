"""Forms for academic structure management."""

from django import forms

from apps.academics.models import (
    Cohort,
    Course,
    CourseVersion,
    Diploma,
    Lecture,
    Semester,
)


class DiplomaForm(forms.ModelForm):
    class Meta:
        model = Diploma
        fields = ["title", "description", "total_semesters", "is_active"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }


class SemesterForm(forms.ModelForm):
    class Meta:
        model = Semester
        fields = ["order", "title", "is_active"]


class CourseForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = ["title", "description", "is_standalone", "is_active"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }


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
