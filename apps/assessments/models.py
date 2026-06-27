from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.academics.models import Cohort, CourseVersion, Semester
from apps.core.models import TimeStampedModel

from .constants import (
    ASSESSMENT_TYPE_CHOICES,
    ATTEMPT_STATUS_ASSIGNED,
    ATTEMPT_STATUS_CHOICES,
    GRADE_VISIBILITY_CHOICES,
    GRADE_VISIBILITY_HIDDEN,
    HOMEWORK_STATUS_CHOICES,
    HOMEWORK_STATUS_UPLOADED,
    QUESTION_TYPE_CHOICES,
    QUESTION_TYPE_MCQ,
)


class Assessment(TimeStampedModel):
    title = models.CharField(_("title"), max_length=255)
    description = models.TextField(_("description"), blank=True)
    assessment_type = models.CharField(
        _("assessment type"),
        max_length=25,
        choices=ASSESSMENT_TYPE_CHOICES,
        db_index=True,
    )
    course_version = models.ForeignKey(
        CourseVersion,
        on_delete=models.CASCADE,
        related_name="assessments",
        verbose_name=_("course version"),
        null=True,
        blank=True,
    )
    semester = models.ForeignKey(
        Semester,
        on_delete=models.CASCADE,
        related_name="assessments",
        verbose_name=_("semester"),
        null=True,
        blank=True,
    )
    cohort = models.ForeignKey(
        Cohort,
        on_delete=models.CASCADE,
        related_name="assessments",
        verbose_name=_("cohort"),
        null=True,
        blank=True,
    )
    open_at = models.DateTimeField(_("open at"))
    close_at = models.DateTimeField(_("close at"))
    duration_minutes = models.PositiveIntegerField(
        _("duration (minutes)"),
        null=True,
        blank=True,
        help_text=_("Leave blank for untimed assessments like homework."),
    )
    total_points = models.DecimalField(
        _("total points"),
        max_digits=7,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    passing_score = models.DecimalField(
        _("passing score"),
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    max_attempts = models.PositiveIntegerField(_("max attempts"), default=1)
    max_upload_files = models.PositiveIntegerField(
        _("max uploaded files"),
        default=1,
        help_text=_("For homework/file assessments: maximum number of files a student may upload."),
    )
    is_active = models.BooleanField(_("active"), default=False)
    show_correct_answers = models.BooleanField(_("show correct answers"), default=False)
    show_written_feedback = models.BooleanField(_("show written feedback"), default=False)
    grade_visibility = models.CharField(
        _("grade visibility"),
        max_length=20,
        choices=GRADE_VISIBILITY_CHOICES,
        default=GRADE_VISIBILITY_HIDDEN,
        db_index=True,
    )
    allow_late_submission = models.BooleanField(_("allow late submission"), default=False)
    penalty_percent = models.DecimalField(
        _("penalty percent"),
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text=_("Percentage deducted for wrong MCQ answers. 0 means no penalty."),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_assessments",
        verbose_name=_("created by"),
    )

    class Meta:
        verbose_name = _("assessment")
        verbose_name_plural = _("assessments")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["assessment_type"]),
            models.Index(fields=["course_version"]),
            models.Index(fields=["semester"]),
            models.Index(fields=["cohort"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["open_at"]),
            models.Index(fields=["close_at"]),
            models.Index(fields=["grade_visibility"]),
        ]

    def __str__(self):
        return self.title

    def clean(self):

        if self.open_at and self.close_at and self.open_at >= self.close_at:
            raise ValidationError(_("Open time must be before close time."))
        if self.duration_minutes is not None and self.duration_minutes < 1:
            raise ValidationError(_("Duration must be positive for timed assessments."))
        if self.max_attempts < 1:
            raise ValidationError(_("Max attempts must be at least 1."))
        if self.max_upload_files < 1:
            raise ValidationError(_("Max uploaded files must be at least 1."))


class AssessmentQuestion(TimeStampedModel):
    assessment = models.ForeignKey(
        Assessment,
        on_delete=models.CASCADE,
        related_name="questions",
        verbose_name=_("assessment"),
    )
    text = models.TextField(_("question text"))
    question_type = models.CharField(
        _("type"),
        max_length=15,
        choices=QUESTION_TYPE_CHOICES,
        default=QUESTION_TYPE_MCQ,
    )
    points = models.DecimalField(
        _("points"),
        max_digits=5,
        decimal_places=2,
        default=Decimal("1.00"),
        validators=[MinValueValidator(0)],
    )
    order = models.PositiveIntegerField(_("order"), db_index=True)
    required = models.BooleanField(_("required"), default=True)
    rubric = models.TextField(_("rubric"), blank=True, help_text=_("Optional grading rubric for staff."))

    class Meta:
        verbose_name = _("assessment question")
        verbose_name_plural = _("assessment questions")
        ordering = ("order",)
        constraints = [
            models.UniqueConstraint(
                fields=("assessment", "order"),
                name="unique_assessment_question_order",
            ),
        ]

    def __str__(self):
        return self.text[:50]

    def clean(self):
        if self.question_type != QUESTION_TYPE_MCQ and self.pk:
            self.choices.all().delete()


class AssessmentChoice(TimeStampedModel):
    question = models.ForeignKey(
        AssessmentQuestion,
        on_delete=models.CASCADE,
        related_name="choices",
        verbose_name=_("question"),
    )
    text = models.CharField(_("text"), max_length=500)
    is_correct = models.BooleanField(_("correct"), default=False)
    order = models.PositiveIntegerField(_("order"), db_index=True)

    class Meta:
        verbose_name = _("assessment choice")
        verbose_name_plural = _("assessment choices")
        ordering = ("order",)
        constraints = [
            models.UniqueConstraint(
                fields=("question", "order"),
                name="unique_assessment_choice_order",
            ),
        ]

    def __str__(self):
        return self.text[:50]


class AssessmentAttempt(TimeStampedModel):
    assessment = models.ForeignKey(
        Assessment,
        on_delete=models.CASCADE,
        related_name="attempts",
        verbose_name=_("assessment"),
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="assessment_attempts",
        verbose_name=_("student"),
    )
    attempt_number = models.PositiveIntegerField(_("attempt number"), default=1)
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=ATTEMPT_STATUS_CHOICES,
        default=ATTEMPT_STATUS_ASSIGNED,
        db_index=True,
    )
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    submitted_at = models.DateTimeField(_("submitted at"), null=True, blank=True)
    graded_at = models.DateTimeField(_("graded at"), null=True, blank=True)
    graded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="graded_assessment_attempts",
        verbose_name=_("graded by"),
    )
    score_points = models.DecimalField(
        _("score (points)"),
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
    )
    score_percent = models.DecimalField(
        _("score (percent)"),
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    is_pass = models.BooleanField(_("passed"), null=True, blank=True)
    visible_to_student = models.BooleanField(_("visible to student"), default=False)

    class Meta:
        verbose_name = _("assessment attempt")
        verbose_name_plural = _("assessment attempts")
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("assessment", "student", "attempt_number"),
                name="unique_assessment_student_attempt",
            ),
        ]
        indexes = [
            models.Index(fields=["student"]),
            models.Index(fields=["assessment"]),
            models.Index(fields=["status"]),
            models.Index(fields=["submitted_at"]),
            models.Index(fields=["graded_at"]),
        ]

    def __str__(self):
        return f"{self.student} - {self.assessment} (#{self.attempt_number})"


class AssessmentAnswer(TimeStampedModel):
    attempt = models.ForeignKey(
        AssessmentAttempt,
        on_delete=models.CASCADE,
        related_name="answers",
        verbose_name=_("attempt"),
    )
    question = models.ForeignKey(
        AssessmentQuestion,
        on_delete=models.CASCADE,
        related_name="answers",
        verbose_name=_("question"),
    )
    selected_choice = models.ForeignKey(
        AssessmentChoice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="answers",
        verbose_name=_("selected choice"),
    )
    written_text = models.TextField(_("written text"), blank=True)
    score = models.DecimalField(
        _("score"),
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    feedback = models.TextField(_("feedback"), blank=True)
    graded_at = models.DateTimeField(_("graded at"), null=True, blank=True)
    score_override_reason = models.TextField(
        _("score override reason"), blank=True,
        help_text=_("Reason for overriding the score. Required when changing an already-graded answer."),
    )
    graded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="graded_assessment_answers",
        verbose_name=_("graded by"),
    )

    class Meta:
        verbose_name = _("assessment answer")
        verbose_name_plural = _("assessment answers")
        constraints = [
            models.UniqueConstraint(
                fields=("attempt", "question"),
                name="unique_assessment_attempt_question",
            ),
        ]
        ordering = ("question__order",)

    def __str__(self):
        return f"Answer to Q{self.question.order} by {self.attempt.student}"


class HomeworkSubmission(TimeStampedModel):
    assessment = models.ForeignKey(
        Assessment,
        on_delete=models.CASCADE,
        related_name="homework_submissions",
        verbose_name=_("assessment"),
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="homework_submissions",
        verbose_name=_("student"),
    )
    attempt = models.ForeignKey(
        AssessmentAttempt,
        on_delete=models.CASCADE,
        related_name="homework_submissions",
        verbose_name=_("attempt"),
        null=True,
        blank=True,
    )
    original_filename = models.CharField(_("original filename"), max_length=500)
    storage_key = models.CharField(_("storage key"), max_length=500)
    content_type = models.CharField(_("content type"), max_length=100)
    file_size = models.PositiveIntegerField(_("file size (bytes)"))
    checksum = models.CharField(_("checksum"), max_length=64, blank=True)
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=HOMEWORK_STATUS_CHOICES,
        default=HOMEWORK_STATUS_UPLOADED,
        db_index=True,
    )
    submitted_at = models.DateTimeField(_("submitted at"), null=True, blank=True)
    reviewed_at = models.DateTimeField(_("reviewed at"), null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_homework",
        verbose_name=_("reviewed by"),
    )
    score = models.DecimalField(
        _("score"),
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    feedback = models.TextField(_("feedback"), blank=True)

    class Meta:
        verbose_name = _("homework submission")
        verbose_name_plural = _("homework submissions")
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.student} - {self.assessment} - {self.original_filename}"
