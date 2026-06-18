from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.exams.constants import (
    ASSIGNMENT_STATUS_ASSIGNED,
    ASSIGNMENT_STATUS_CHOICES,
    EXAM_TYPE_CHOICES,
    EXAM_TYPE_ENTRANCE,
    QUESTION_TYPE_CHOICES,
    QUESTION_TYPE_MCQ,
)


class Exam(TimeStampedModel):
    title = models.CharField(_("title"), max_length=255)
    description = models.TextField(_("description"), blank=True)
    exam_type = models.CharField(
        _("exam type"),
        max_length=20,
        choices=EXAM_TYPE_CHOICES,
        default=EXAM_TYPE_ENTRANCE,
        db_index=True,
    )
    duration_minutes = models.PositiveIntegerField(_("duration (minutes)"))
    passing_score = models.DecimalField(
        _("passing score"),
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    total_points = models.DecimalField(
        _("total points"),
        max_digits=7,
        decimal_places=2,
        default=0,
    )
    is_active = models.BooleanField(_("active"), default=False)
    open_at = models.DateTimeField(_("open at"))
    close_at = models.DateTimeField(_("close at"))
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_exams",
        verbose_name=_("created by"),
    )

    class Meta:
        verbose_name = _("exam")
        verbose_name_plural = _("exams")
        ordering = ("-created_at",)

    def __str__(self):
        return self.title


class Question(TimeStampedModel):
    exam = models.ForeignKey(
        Exam,
        on_delete=models.CASCADE,
        related_name="questions",
        verbose_name=_("exam"),
    )
    text = models.TextField(_("question text"))
    question_type = models.CharField(
        _("type"),
        max_length=10,
        choices=QUESTION_TYPE_CHOICES,
        default=QUESTION_TYPE_MCQ,
    )
    points = models.DecimalField(
        _("points"),
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    order = models.PositiveIntegerField(_("order"), db_index=True)

    class Meta:
        verbose_name = _("question")
        verbose_name_plural = _("questions")
        ordering = ("order",)

    def __str__(self):
        return self.text[:50]


class Choice(TimeStampedModel):
    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name="choices",
        verbose_name=_("question"),
    )
    text = models.CharField(_("text"), max_length=500)
    is_correct = models.BooleanField(_("correct"), default=False)
    order = models.PositiveIntegerField(_("order"), db_index=True)

    class Meta:
        verbose_name = _("choice")
        verbose_name_plural = _("choices")
        ordering = ("order",)

    def __str__(self):
        return self.text[:50]


class Assignment(TimeStampedModel):
    exam = models.ForeignKey(
        Exam,
        on_delete=models.CASCADE,
        related_name="assignments",
        verbose_name=_("exam"),
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="exam_assignments",
        verbose_name=_("student"),
    )
    attempt_number = models.PositiveIntegerField(_("attempt number"), default=1)
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=ASSIGNMENT_STATUS_CHOICES,
        default=ASSIGNMENT_STATUS_ASSIGNED,
        db_index=True,
    )
    score = models.DecimalField(
        _("score"),
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    assigned_at = models.DateTimeField(_("assigned at"), auto_now_add=True)
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    submitted_at = models.DateTimeField(_("submitted at"), null=True, blank=True)
    graded_at = models.DateTimeField(_("graded at"), null=True, blank=True)
    graded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="graded_assignments",
        verbose_name=_("graded by"),
    )

    class Meta:
        verbose_name = _("exam assignment")
        verbose_name_plural = _("exam assignments")
        ordering = ("-assigned_at",)

    def __str__(self):
        return f"{self.student} - {self.exam}"

    @property
    def is_pass(self):
        if self.score is None or self.exam.passing_score is None:
            return None
        return self.score >= self.exam.passing_score


class Answer(TimeStampedModel):
    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.CASCADE,
        related_name="answers",
        verbose_name=_("assignment"),
    )
    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name="answers",
        verbose_name=_("question"),
    )
    selected_choice = models.ForeignKey(
        Choice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="answers",
        verbose_name=_("selected choice"),
    )
    essay_text = models.TextField(_("essay text"), blank=True)
    score = models.DecimalField(
        _("score"),
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    feedback = models.TextField(_("feedback"), blank=True)
    graded_at = models.DateTimeField(_("graded at"), null=True, blank=True)
    graded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="graded_answers",
        verbose_name=_("graded by"),
    )

    class Meta:
        verbose_name = _("answer")
        verbose_name_plural = _("answers")
        constraints = [
            models.UniqueConstraint(
                fields=("assignment", "question"),
                name="unique_assignment_question",
            ),
        ]
        ordering = ("question__order",)

    def __str__(self):
        return f"Answer to Q{self.question.order} by {self.assignment.student}"
