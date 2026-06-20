from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.applications.models import ProgramApplication
from apps.core.models import TimeStampedModel

from .constants import (
    ATTEMPT_STATUS_ASSIGNED,
    ATTEMPT_STATUS_CHOICES,
    GEMINI_DECISION_CHOICES,
    QUESTION_TYPE_CHOICES,
    QUESTION_TYPE_MCQ,
)


class EntranceExam(TimeStampedModel):
    title = models.CharField(_("title"), max_length=255)
    description = models.TextField(_("description"), blank=True)
    diploma = models.ForeignKey(
        "academics.Diploma",
        on_delete=models.PROTECT,
        related_name="entrance_exams",
        verbose_name=_("diploma"),
    )
    cohort = models.ForeignKey(
        "academics.Cohort",
        on_delete=models.PROTECT,
        related_name="entrance_exams",
        verbose_name=_("cohort"),
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
        related_name="created_entrance_exams",
        verbose_name=_("created by"),
    )

    class Meta:
        verbose_name = _("entrance exam")
        verbose_name_plural = _("entrance exams")
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("diploma", "cohort"),
                name="unique_entrance_exam_per_cohort",
            ),
        ]

    def __str__(self):
        return f"{self.title} — {self.cohort}"


class EntranceQuestion(TimeStampedModel):
    exam = models.ForeignKey(
        EntranceExam,
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
    rubric = models.TextField(
        _("scoring rubric / model answer"),
        blank=True,
        help_text=_("Instructions for scoring this question. Sent to Gemini for weighted scoring."),
    )

    class Meta:
        verbose_name = _("entrance question")
        verbose_name_plural = _("entrance questions")
        ordering = ("order",)
        constraints = [
            models.UniqueConstraint(
                fields=("exam", "order"),
                name="unique_entrance_question_order",
            ),
        ]

    def __str__(self):
        return self.text[:50]


class EntranceChoice(TimeStampedModel):
    question = models.ForeignKey(
        EntranceQuestion,
        on_delete=models.CASCADE,
        related_name="choices",
        verbose_name=_("question"),
    )
    text = models.CharField(_("text"), max_length=500)
    is_correct = models.BooleanField(_("correct"), default=False)
    order = models.PositiveIntegerField(_("order"), db_index=True)

    class Meta:
        verbose_name = _("entrance choice")
        verbose_name_plural = _("entrance choices")
        ordering = ("order",)
        constraints = [
            models.UniqueConstraint(
                fields=("question", "order"),
                name="unique_entrance_choice_order",
            ),
        ]

    def __str__(self):
        return self.text[:50]


class EntranceAttempt(TimeStampedModel):
    exam = models.ForeignKey(
        EntranceExam,
        on_delete=models.CASCADE,
        related_name="attempts",
        verbose_name=_("exam"),
    )
    program_application = models.ForeignKey(
        ProgramApplication,
        on_delete=models.PROTECT,
        related_name="entrance_attempts",
        verbose_name=_("program application"),
    )
    student = models.ForeignKey(
        "students.StudentProfile",
        on_delete=models.PROTECT,
        related_name="entrance_attempts",
        verbose_name=_("student"),
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=ATTEMPT_STATUS_CHOICES,
        default=ATTEMPT_STATUS_ASSIGNED,
        db_index=True,
    )
    score = models.DecimalField(
        _("score"),
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    result = models.CharField(
        _("result"),
        max_length=10,
        choices=(("pass", _("Pass")), ("fail", _("Fail"))),
        blank=True,
        default="",
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
        related_name="graded_entrance_attempts",
        verbose_name=_("graded by"),
    )

    class Meta:
        verbose_name = _("entrance attempt")
        verbose_name_plural = _("entrance attempts")
        ordering = ("-assigned_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("program_application", "exam"),
                name="unique_attempt_per_application_exam",
            ),
        ]

    def __str__(self):
        return f"{self.student} — {self.exam}"


class EntranceAnswer(TimeStampedModel):
    attempt = models.ForeignKey(
        EntranceAttempt,
        on_delete=models.CASCADE,
        related_name="answers",
        verbose_name=_("attempt"),
    )
    question = models.ForeignKey(
        EntranceQuestion,
        on_delete=models.CASCADE,
        related_name="answers",
        verbose_name=_("question"),
    )
    selected_choice = models.ForeignKey(
        EntranceChoice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="answers",
        verbose_name=_("selected choice"),
    )
    written_text = models.TextField(_("written answer"), blank=True)
    gemini_suggested_score = models.DecimalField(
        _("Gemini suggested score"),
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    gemini_suggested_feedback = models.TextField(
        _("Gemini suggested feedback"),
        blank=True,
    )
    gemini_request_ref = models.CharField(
        _("Gemini request reference"),
        max_length=255,
        blank=True,
    )
    gemini_decision = models.CharField(
        _("Gemini decision"),
        max_length=20,
        choices=GEMINI_DECISION_CHOICES,
        blank=True,
    )
    score = models.DecimalField(
        _("final score"),
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
        related_name="graded_entrance_answers",
        verbose_name=_("graded by"),
    )

    class Meta:
        verbose_name = _("entrance answer")
        verbose_name_plural = _("entrance answers")
        constraints = [
            models.UniqueConstraint(
                fields=("attempt", "question"),
                name="unique_entrance_answer_question",
            ),
        ]
        ordering = ("question__order",)

    def __str__(self):
        return f"Answer to Q{self.question.order} by {self.attempt.student}"
