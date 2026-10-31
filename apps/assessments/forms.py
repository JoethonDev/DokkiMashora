"""Forms for the assessments app."""

from __future__ import annotations

from types import SimpleNamespace

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.academics.models import Cohort, Course, CourseVersion, Diploma, DiplomaLecture, Semester
from apps.assessments.constants import (
    ASSESSMENT_TYPE_ACTIVE_CHOICES,
    ASSESSMENT_TYPE_HOMEWORK,
    ASSESSMENT_TYPE_QUIZ,
    ASSESSMENT_TYPE_SEMESTER_EXAM,
    QUESTION_TYPE_MCQ,
    QUESTION_TYPE_WRITTEN,
    RELEASE_MODE_AUTOMATIC,
    RELEASE_MODE_MANUAL,
)
from apps.core.forms import StyledFormMixin
from apps.core.permissions import safe_int

from .models import (
    Assessment,
    AssessmentLectureSection,
    AssessmentQuestion,
    AssessmentSectionManualQuestion,
    DiplomaLectureQuestion,
)


class AssessmentLectureSectionForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = AssessmentLectureSection
        fields = ["lecture", "order", "mcq_count"]
        widgets = {
            "lecture": forms.Select(attrs={}),
            "order": forms.NumberInput(attrs={}),
            "mcq_count": forms.NumberInput(attrs={}),
        }
        labels = {
            "order": _("Section order (number)"),
            "mcq_count": _("MCQ question count (number)"),
        }
        help_texts = {
            "order": _("e.g., 1"),
            "mcq_count": _("e.g., 5"),
        }

    def __init__(self, *args, assessment=None, **kwargs):
        super().__init__(*args, **kwargs)
        if assessment:
            self._assessment = assessment
            self.fields["lecture"].queryset = DiplomaLecture.objects.filter(
                cohort=assessment.cohort, semester=assessment.semester,
            )
        else:
            self.fields["lecture"].queryset = DiplomaLecture.objects.none()

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
        if lecture and (not mcq_count or mcq_count == 0) and not (self.instance.pk and self.instance.manual_questions.exists()):
            available = DiplomaLectureQuestion.objects.filter(
                lecture=lecture, active=True, question_type=QUESTION_TYPE_MCQ,
            ).count()
            if available == 0 and not (self.instance.pk and self.instance.manual_questions.exists()):
                self.add_error("mcq_count", _(
                    "Set at least one MCQ or add manual questions to determine max grade."
                ))
        return cleaned


class AssessmentSectionManualQuestionForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = AssessmentSectionManualQuestion
        fields = ["text", "points", "order", "required", "rubric"]
        widgets = {
            "text": forms.Textarea(attrs={"rows": 3}),
            "rubric": forms.Textarea(attrs={"rows": 2}),
            "points": forms.NumberInput(attrs={}),
            "order": forms.NumberInput(attrs={}),
        }
        labels = {
            "points": _("Points available (number)"),
            "order": _("Question order (number)"),
        }
        help_texts = {
            "points": _("e.g., 10"),
            "order": _("e.g., 1"),
        }

    def save(self, commit=True):
        question = super().save(commit=False)
        question.question_type = QUESTION_TYPE_WRITTEN
        if commit:
            question.save()
        return question

SCOPE_COURSE = "course"
SCOPE_DIPLOMA = "diploma"

SCOPE_CHOICES = [
    (SCOPE_COURSE, _("Course")),
    (SCOPE_DIPLOMA, _("Diploma")),
]


def assessment_type_choices_for_scope(scope_type):
    """Return only the assessment types the selected scope supports."""
    if scope_type == SCOPE_COURSE:
        allowed = {ASSESSMENT_TYPE_QUIZ}
    elif scope_type == SCOPE_DIPLOMA:
        allowed = {ASSESSMENT_TYPE_HOMEWORK, ASSESSMENT_TYPE_SEMESTER_EXAM}
    else:
        return ASSESSMENT_TYPE_ACTIVE_CHOICES
    return [choice for choice in ASSESSMENT_TYPE_ACTIVE_CHOICES if choice[0] in allowed]


class AssessmentTypeSelect(forms.Select):
    """Tag type options with their scope so the form can filter them live."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(
            name, value, label, selected, index, subindex=subindex, attrs=attrs,
        )
        if value:
            option["attrs"]["data-scope"] = (
                SCOPE_COURSE if value == ASSESSMENT_TYPE_QUIZ else SCOPE_DIPLOMA
            )
        return option


class AssessmentForm(StyledFormMixin, forms.ModelForm):
    scope_type = forms.ChoiceField(choices=SCOPE_CHOICES, label=_("Scope"))
    course = forms.ModelChoiceField(
        queryset=Course.objects.filter(is_active=True),
        required=False, label=_("Course"),
    )
    diploma = forms.ModelChoiceField(
        queryset=Diploma.objects.filter(is_active=True), required=False, label=_("Diploma"),
    )

    class Meta:
        model = Assessment
        fields = [
            "scope_type",
            "title",
            "description",
            "assessment_type",
            "is_completion_exam",
            "is_required_for_semester_evaluation",
            "course",
            "diploma",
            "course_version",
            "semester",
            "cohort",
            "open_at",
            "close_at",
            "duration_minutes",
            "passing_score",
            "max_upload_files",
            "is_active",
            "show_written_feedback",
            "release_mode",
            "allow_late_submission",
            "penalty_percent",
        ]
        widgets = {
            "title": forms.TextInput(attrs={}),
            "description": forms.Textarea(attrs={"rows": 3}),
            "assessment_type": AssessmentTypeSelect(attrs={}),
            "course_version": forms.Select(attrs={}),
            "semester": forms.Select(attrs={}),
            "cohort": forms.Select(attrs={}),
            "open_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "close_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "duration_minutes": forms.NumberInput(attrs={}),
            "passing_score": forms.NumberInput(attrs={}),
            "max_upload_files": forms.NumberInput(attrs={}),
            "release_mode": forms.Select(attrs={}),
            "penalty_percent": forms.NumberInput(attrs={}),
        }
        labels = {
            "passing_score": _("Passing score (%)"),
            "penalty_percent": _("Wrong-answer penalty (%)"),
            "is_completion_exam": _("End-course exam"),
        }
        help_texts = {
            "passing_score": _("0–100"),
            "penalty_percent": _("0–100"),
            "is_completion_exam": _(
                "Passing this exam is required to complete the course. Only one quiz per course version can be the end-course exam."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = self.instance
        if self.is_bound:
            scope_type = self.data.get("scope_type")
        elif instance.pk:
            scope_type = (
                SCOPE_COURSE if instance.course_version_id else SCOPE_DIPLOMA
            )
        else:
            scope_type = None
        # Active vocabulary only: a course offers Quiz only, a diploma offers
        # Homework / Semester Exam. Deprecated types (final_exam,
        # additional_assessment) are historical and never offered (P2-01).
        self.fields["assessment_type"].choices = assessment_type_choices_for_scope(
            scope_type,
        )
        # Locked release defaults on create (P2-04): Course Quiz = Automatic,
        # Diploma Homework/Semester Exam = Manual; both remain configurable.
        if not instance:
            scope = self.data.get("scope_type") if self.is_bound else None
            atype = self.data.get("assessment_type") if self.is_bound else None
            if scope == SCOPE_COURSE and atype == ASSESSMENT_TYPE_QUIZ:
                self.fields["release_mode"].initial = RELEASE_MODE_AUTOMATIC
            else:
                self.fields["release_mode"].initial = RELEASE_MODE_MANUAL
        if instance:
            if instance.course_version_id:
                self.fields["scope_type"].initial = SCOPE_COURSE
                cv = instance.course_version
                if cv:
                    self.fields["course"].initial = cv.course
            elif instance.semester_id or instance.cohort_id:
                self.fields["scope_type"].initial = SCOPE_DIPLOMA
                if instance.semester_id:
                    self.fields["semester"].initial = instance.semester_id
                    if instance.semester:
                        self.fields["diploma"].initial = instance.semester.diploma_id
                        self.fields["diploma"].queryset = Diploma.objects.filter(is_active=True, id=instance.semester.diploma_id)
                if instance.cohort_id:
                    self.fields["cohort"].initial = instance.cohort_id
        selected_course_id = self.data.get("course") if self.is_bound else self.fields["course"].initial
        selected_diploma_id = self.data.get("diploma") if self.is_bound else self.fields["diploma"].initial
        if hasattr(selected_course_id, "pk"):
            selected_course_id = selected_course_id.pk
        if hasattr(selected_diploma_id, "pk"):
            selected_diploma_id = selected_diploma_id.pk
        selected_course_id = safe_int(str(selected_course_id)) if selected_course_id is not None else None
        selected_diploma_id = safe_int(str(selected_diploma_id)) if selected_diploma_id is not None else None
        self.fields["course_version"].queryset = CourseVersion.objects.filter(
            is_active=True, course_id=selected_course_id,
        ) if selected_course_id else CourseVersion.objects.none()
        self.fields["semester"].queryset = Semester.objects.filter(
            is_active=True, diploma_id=selected_diploma_id,
        ) if selected_diploma_id else Semester.objects.none()
        self.fields["cohort"].queryset = Cohort.objects.filter(
            is_active=True, diploma_id=selected_diploma_id,
        ) if selected_diploma_id else Cohort.objects.none()
        self.fields["semester"].required = False
        self.fields["cohort"].required = False
        self.fields["course_version"].required = False

    def clean(self):
        cleaned = super().clean()
        scope_type = cleaned.get("scope_type")
        course = cleaned.get("course")
        semester = cleaned.get("semester")
        cohort = cleaned.get("cohort")
        assessment_type = cleaned.get("assessment_type")

        if scope_type == SCOPE_COURSE:
            if not course:
                raise forms.ValidationError(
                    _("Course is required for course-scoped assessments.")
                )
            cleaned["semester"] = None
            cleaned["cohort"] = None
            cv = cleaned.get("course_version")
            if cv and cv.course_id != course.id:
                self.add_error("course_version", _("Selected course version does not belong to the selected course."))
                cv = None
            cv = cv or course.versions.filter(is_active=True).first()
            if not cv:
                raise forms.ValidationError(
                    _("Selected course has no active version.")
                )
            cleaned["course_version"] = cv
            if assessment_type != ASSESSMENT_TYPE_QUIZ:
                self.add_error(
                    "assessment_type",
                    _("Course-scoped assessments can only be a Quiz."),
                )
                cleaned["is_completion_exam"] = False
            elif cleaned.get("is_completion_exam"):
                if not cv.course.video_tracking_enabled:
                    self.add_error(
                        "is_completion_exam",
                        _("Enable lecture tracking for this course before designating an end-course exam."),
                    )
                conflicting_exam = Assessment.objects.filter(
                    course_version=cv,
                    is_completion_exam=True,
                )
                if self.instance.pk:
                    conflicting_exam = conflicting_exam.exclude(pk=self.instance.pk)
                if conflicting_exam.exists():
                    self.add_error(
                        "is_completion_exam",
                        _("This course version already has an end-course exam."),
                    )
        elif scope_type == SCOPE_DIPLOMA:
            if not semester or not cohort:
                self.add_error(
                    "semester",
                    _("Both semester and cohort are required for diploma-scoped assessments."),
                )
            cleaned["course_version"] = None
            cleaned["is_completion_exam"] = False
            if semester and cohort and semester.diploma_id != cohort.diploma_id:
                raise forms.ValidationError(
                    _("Semester and cohort must belong to the same diploma.")
                )
            if assessment_type not in (ASSESSMENT_TYPE_HOMEWORK, ASSESSMENT_TYPE_SEMESTER_EXAM):
                self.add_error(
                    "assessment_type",
                    _("Diploma-scoped assessments can only be Homework or Semester Exam."),
                )
        else:
            raise forms.ValidationError(
                _("Please select a scope (course or diploma).")
            )

        if scope_type != SCOPE_DIPLOMA or assessment_type not in (
            ASSESSMENT_TYPE_HOMEWORK,
            ASSESSMENT_TYPE_SEMESTER_EXAM,
        ):
            cleaned["is_required_for_semester_evaluation"] = True

        # Locked active assessments allow exactly one attempt (P2-01); the
        # field is not user-configurable.
        cleaned["max_attempts"] = 1

        # Safety net: never persist an empty release mode (P2-04).
        if cleaned.get("release_mode") not in (RELEASE_MODE_AUTOMATIC, RELEASE_MODE_MANUAL):
            cleaned["release_mode"] = RELEASE_MODE_MANUAL

        open_at = cleaned.get("open_at")
        close_at = cleaned.get("close_at")
        if open_at and close_at and open_at >= close_at:
            raise forms.ValidationError(_("Open time must be before close time."))
        duration = cleaned.get("duration_minutes")
        if duration is not None and duration < 1:
            raise forms.ValidationError(_("Duration must be positive."))
        if cleaned.get("assessment_type") != "homework":
            cleaned["max_upload_files"] = 1
            cleaned["allow_late_submission"] = False
        elif cleaned.get("max_upload_files", 0) < 1:
            self.add_error("max_upload_files", _("Max uploaded files must be at least 1."))
        return cleaned


class QuestionForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = AssessmentQuestion
        fields = ["text", "question_type", "points", "required", "rubric"]
        widgets = {
            "text": forms.Textarea(attrs={"rows": 3}),
            "question_type": forms.Select(attrs={}),
            "points": forms.NumberInput(attrs={}),
            "rubric": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {
            "points": _("Points available (number)"),
        }
        help_texts = {
            "points": _("e.g., 10"),
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


class DiplomaLectureQuestionForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = DiplomaLectureQuestion
        fields = ["text", "question_type", "points", "active"]
        widgets = {
            "text": forms.Textarea(attrs={"rows": 3}),
            "question_type": forms.Select(attrs={}),
            "points": forms.NumberInput(attrs={}),
            "active": forms.CheckboxInput(attrs={}),
        }
        labels = {
            "points": _("Points available (number)"),
        }
        help_texts = {
            "points": _("e.g., 10"),
        }
