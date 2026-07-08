"""Forms for the assessments app."""

from __future__ import annotations

from types import SimpleNamespace

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.academics.models import Cohort, Course, Diploma, DiplomaLecture, Semester
from apps.assessments.constants import QUESTION_TYPE_MCQ, QUESTION_TYPE_WRITTEN

from .models import (
    Assessment,
    AssessmentLectureSection,
    AssessmentQuestion,
    AssessmentSectionManualQuestion,
    DiplomaLectureQuestion,
)


class AssessmentLectureSectionForm(forms.ModelForm):
    class Meta:
        model = AssessmentLectureSection
        fields = ["lecture", "order", "mcq_count", "max_grade"]

    def __init__(self, *args, assessment=None, **kwargs):
        super().__init__(*args, **kwargs)
        if assessment:
            self._assessment = assessment
            self.fields["lecture"].queryset = DiplomaLecture.objects.filter(
                cohort=assessment.cohort, semester=assessment.semester,
            )
        else:
            self.fields["lecture"].queryset = DiplomaLecture.objects.none()

    def clean_max_grade(self):
        val = self.cleaned_data["max_grade"]
        if val <= 0:
            raise forms.ValidationError(_("Max grade must be positive."))
        return val

    def clean_mcq_count(self):
        val = self.cleaned_data["mcq_count"]
        if val < 0:
            raise forms.ValidationError(_("MCQ count cannot be negative."))
        return val

    def clean(self):
        cleaned = super().clean()
        assessment = getattr(self, "_assessment", None) or (self.instance.assessment_id and self.instance.assessment)
        if assessment and assessment.course_version_id:
            self.add_error(None, _("Lecture sections are only for diploma-scoped assessments."))
            return cleaned
        lecture = cleaned.get("lecture")
        mcq_count = cleaned.get("mcq_count")
        if lecture and mcq_count and mcq_count > 0:
            available = DiplomaLectureQuestion.objects.filter(
                lecture=lecture, active=True, question_type=QUESTION_TYPE_MCQ,
            ).count()
            if mcq_count > available:
                self.add_error("mcq_count", _(
                    "Only %(available)d active MCQ(s) available in this lecture's bank."
                ) % {"available": available})
        return cleaned


class AssessmentSectionManualQuestionForm(forms.ModelForm):
    question_type = forms.CharField(widget=forms.HiddenInput(), initial=QUESTION_TYPE_WRITTEN)

    class Meta:
        model = AssessmentSectionManualQuestion
        fields = ["text", "question_type", "points", "order", "required", "rubric"]
        widgets = {
            "text": forms.Textarea(attrs={"rows": 3}),
            "rubric": forms.Textarea(attrs={"rows": 2}),
        }

SCOPE_COURSE = "course"
SCOPE_DIPLOMA = "diploma"

SCOPE_CHOICES = [
    (SCOPE_COURSE, _("Course")),
    (SCOPE_DIPLOMA, _("Diploma")),
]


class AssessmentForm(forms.ModelForm):
    scope_type = forms.ChoiceField(choices=SCOPE_CHOICES, label=_("Scope"))
    standalone_course = forms.ModelChoiceField(
        queryset=Course.objects.filter(is_standalone=True, is_active=True),
        required=False, label=_("Standalone course"),
    )
    diploma = forms.ModelChoiceField(
        queryset=Diploma.objects.filter(is_active=True), required=False, label=_("Diploma"),
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
                cv = instance.course_version
                if cv and cv.course.is_standalone:
                    self.fields["standalone_course"].initial = cv.course
            elif instance.semester_id or instance.cohort_id:
                self.fields["scope_type"].initial = SCOPE_DIPLOMA
        if diploma_id:
            self.fields["semester"].queryset = Semester.objects.filter(diploma_id=diploma_id, is_active=True)
            self.fields["cohort"].queryset = Cohort.objects.filter(diploma_id=diploma_id, is_active=True)
            self.fields["diploma"].initial = diploma_id
        else:
            self.fields["semester"].queryset = Semester.objects.none()
            self.fields["cohort"].queryset = Cohort.objects.none()
        self.fields["semester"].required = False
        self.fields["cohort"].required = False
        self.fields["course_version"].required = False

    def clean(self):
        cleaned = super().clean()
        scope_type = cleaned.get("scope_type")
        standalone_course = cleaned.get("standalone_course")
        semester = cleaned.get("semester")
        cohort = cleaned.get("cohort")

        if scope_type == SCOPE_COURSE:
            if not standalone_course:
                raise forms.ValidationError(
                    _("Standalone course is required for course-scoped assessments.")
                )
            cleaned["semester"] = None
            cleaned["cohort"] = None
            cv = standalone_course.versions.filter(is_active=True).first()
            if not cv:
                raise forms.ValidationError(
                    _("Selected standalone course has no active version.")
                )
            cleaned["course_version"] = cv
        elif scope_type == SCOPE_DIPLOMA:
            if not semester or not cohort:
                self.add_error(
                    "semester",
                    _("Both semester and cohort are required for diploma-scoped assessments."),
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


class DiplomaLectureQuestionForm(forms.ModelForm):
    class Meta:
        model = DiplomaLectureQuestion
        fields = ["text", "question_type", "points", "active"]
        widgets = {
            "text": forms.Textarea(attrs={"rows": 3}),
        }
