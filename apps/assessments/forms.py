"""Forms for the assessments app."""

from __future__ import annotations

from types import SimpleNamespace

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Assessment, AssessmentQuestion


class AssessmentForm(forms.ModelForm):
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

    def clean(self):
        cleaned = super().clean()
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
