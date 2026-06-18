from django import forms
from django.utils.translation import gettext_lazy as _

from apps.exams.models import Exam, Question


class ExamForm(forms.ModelForm):
    class Meta:
        model = Exam
        fields = [
            "title",
            "description",
            "exam_type",
            "duration_minutes",
            "passing_score",
            "open_at",
            "close_at",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "open_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "close_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
        }
        help_texts = {
            "open_at": _("Date and time when the exam becomes available."),
            "close_at": _("Date and time when the exam closes."),
        }

    def clean(self):
        cleaned = super().clean()
        open_at = cleaned.get("open_at")
        close_at = cleaned.get("close_at")
        if open_at and close_at and open_at >= close_at:
            raise forms.ValidationError(_("Open time must be before close time."))
        return cleaned


class QuestionForm(forms.ModelForm):
    class Meta:
        model = Question
        fields = [
            "text",
            "question_type",
            "points",
        ]
        widgets = {
            "text": forms.Textarea(attrs={"rows": 5}),
        }
