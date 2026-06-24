"""Forms for the assessments app."""

from __future__ import annotations

from types import SimpleNamespace

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.academics.models import Cohort, Diploma, Semester
from .models import Assessment, AssessmentQuestion

SCOPE_COURSE = "course"
SCOPE_DIPLOMA = "diploma"

SCOPE_CHOICES = [
    (SCOPE_COURSE, _("Course")),
    (SCOPE_DIPLOMA, _("Diploma")),
]


class AssessmentForm(forms.ModelForm):
    scope_type = forms.ChoiceField(choices=SCOPE_CHOICES, label=_("Scope"))
    diploma = forms.ModelChoiceField(
        queryset=Diploma.objects.all(), required=False, label=_("Diploma"),
    )

    class Meta:
        model = Assessment
        fields = [
            "title",
            "description",
            "assessment_type",
            "course_version",
            "semester",
            "cohort",
            "open_at",
            "close_at",
            "duration_minutes",
            "total_points",
            "passing_score",
            "max_attempts",
            "max_upload_files",
            "is_active",
            "show_correct_answers",
            "show_written_feedback",
            "grade_visibility",
            "allow_late_submission",
            "penalty_percent",
        ]
        widgets = {
            "open_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "close_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = kwargs.get("instance")
        data = kwargs.get("data")
        diploma_id = None
        if data and data.get("diploma"):
            diploma_id = data.get("diploma")
        elif instance:
            if instance.semester_id:
                diploma_id = instance.semester.diploma_id
            elif instance.cohort_id:
                diploma_id = instance.cohort.diploma_id
            if instance.course_version_id:
                self.fields["scope_type"].initial = SCOPE_COURSE
            elif instance.semester_id or instance.cohort_id:
                self.fields["scope_type"].initial = SCOPE_DIPLOMA
        if diploma_id:
            self.fields["semester"].queryset = Semester.objects.filter(diploma_id=diploma_id)
            self.fields["cohort"].queryset = Cohort.objects.filter(diploma_id=diploma_id)
        else:
            self.fields["semester"].queryset = Semester.objects.none()
            self.fields["cohort"].queryset = Cohort.objects.none()
        self.fields["semester"].required = False
        self.fields["cohort"].required = False

    def clean(self):
        cleaned = super().clean()
        scope_type = cleaned.get("scope_type")
        course_version = cleaned.get("course_version")
        semester = cleaned.get("semester")
        cohort = cleaned.get("cohort")

        if scope_type == SCOPE_COURSE:
            if not course_version:
                raise forms.ValidationError(
                    _("Course version is required for course-scoped assessments.")
                )
            cleaned["semester"] = None
            cleaned["cohort"] = None
        elif scope_type == SCOPE_DIPLOMA:
            if not semester and not cohort:
                self.add_error(
                    "semester",
                    _("Semester or cohort is required for diploma-scoped assessments."),
                )
            cleaned["course_version"] = None
            if semester and cohort and semester.diploma_id != cohort.diploma_id:
                raise forms.ValidationError(
                    _("Semester and cohort must belong to the same diploma.")
                )
        else:
            raise forms.ValidationError(
                _("Please select a scope (course or diploma).")
            )

        open_at = cleaned.get("open_at")
        close_at = cleaned.get("close_at")
        if open_at and close_at and open_at >= close_at:
            raise forms.ValidationError(_("Open time must be before close time."))
        duration = cleaned.get("duration_minutes")
        if duration is not None and duration < 1:
            raise forms.ValidationError(_("Duration must be positive."))
        max_attempts = cleaned.get("max_attempts")
        if max_attempts is not None and max_attempts < 1:
            raise forms.ValidationError(_("Max attempts must be at least 1."))
        return cleaned


class QuestionForm(forms.ModelForm):
    class Meta:
        model = AssessmentQuestion
        fields = ["text", "question_type", "points", "required", "rubric"]
        widgets = {
            "text": forms.Textarea(attrs={"rows": 3}),
            "rubric": forms.Textarea(attrs={"rows": 2}),
        }


def _choices_for_template(parsed_choices, question):
    if not parsed_choices and question:
        return list(question.choices.all())
    return [
        SimpleNamespace(
            id=choice_data["suffix"],
            text=choice_data["text"],
            is_correct=choice_data["is_correct"],
            order=choice_data["order"] or index,
        )
        for index, choice_data in enumerate(parsed_choices, start=1)
    ]
