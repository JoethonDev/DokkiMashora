import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.exams.constants import (
    ASSIGNMENT_STATUS_ASSIGNED,
    ASSIGNMENT_STATUS_CHOICES,
    QUESTION_TYPE_CHOICES,
    QUESTION_TYPE_ESSAY,
    QUESTION_TYPE_MCQ,
)


class ExamSnapshot(TimeStampedModel):
    """Periodic answer snapshot for auto-recovery when student fails to submit."""
    assignment = models.ForeignKey(
        "Assignment",
        on_delete=models.CASCADE,
        related_name="snapshots",
        verbose_name=_("assignment"),
    )
    question = models.ForeignKey(
        "Question",
        on_delete=models.CASCADE,
        related_name="snapshots",
        verbose_name=_("question"),
    )
    selected_choice = models.ForeignKey(
        "Choice",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="snapshots",
        verbose_name=_("selected choice"),
    )
    essay_text = models.TextField(_("essay text"), blank=True)
    snapshot_at = models.DateTimeField(_("snapshot at"), auto_now=True)

    class Meta:
        verbose_name = _("exam snapshot")
        verbose_name_plural = _("exam snapshots")
        ordering = ("question__order",)
        constraints = [
            models.UniqueConstraint(
                fields=("assignment", "question"),
                name="unique_snapshot_per_assignment_question",
            ),
        ]

    def __str__(self):
        return f"Snapshot Q{self.question.order} for {self.assignment}"


class Exam(TimeStampedModel):
    title = models.CharField(_("title"), max_length=255)
    description = models.TextField(_("description"), blank=True)
    exam_type = models.CharField(
        _("exam type"),
        max_length=20,
        default="assessment",
        db_index=True,
    )
    access_token = models.UUIDField(
        _("access token"), default=uuid.uuid4, unique=True, editable=False,
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

    def regenerate_access_token(self):
        self.access_token = uuid.uuid4()
        self.save(update_fields=["access_token"])


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
        constraints = [
            models.UniqueConstraint(
                fields=("exam", "order"),
                name="unique_question_order_per_exam",
            ),
        ]

    def __str__(self):
        return self.text[:50]

    def clean(self):
        if self.question_type == QUESTION_TYPE_ESSAY and self.pk:
            self.choices.all().delete()


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
        constraints = [
            models.UniqueConstraint(
                fields=("question", "order"),
                name="unique_choice_order_per_question",
            ),
        ]

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


class EmailLog(TimeStampedModel):
    """Tracks exam invitation/notification emails."""
    exam = models.ForeignKey(
        Exam, on_delete=models.CASCADE,
        related_name="email_logs", verbose_name=_("exam"),
    )
    assignment = models.ForeignKey(
        Assignment, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="email_logs",
        verbose_name=_("assignment"),
    )
    recipient = models.EmailField(_("recipient"))
    template_slug = models.CharField(_("template"), max_length=64)
    sent_at = models.DateTimeField(_("sent at"), auto_now_add=True)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="exam_email_logs",
        verbose_name=_("sent by"),
    )
    status = models.CharField(_("status"), max_length=8, default="sent")

    class Meta:
        verbose_name = _("exam email log")
        verbose_name_plural = _("exam email logs")
        ordering = ("-sent_at",)

    def __str__(self):
        return f"{self.recipient} — {self.template_slug} ({self.status})"
