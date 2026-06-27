
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.academics.models import Cohort, Diploma, Semester
from apps.core.models import TimeStampedModel
from apps.students.models import StudentProfile

from .constants import (
    DIPLOMA_COMPLETION_ACTIVE,
    DIPLOMA_COMPLETION_CHOICES,
    PROMOTION_DECISION_CHOICES,
    SEMESTER_PROGRESS_CHOICES,
    SEMESTER_PROGRESS_NOT_STARTED,
)


class DiplomaProgressionRule(TimeStampedModel):
    diploma = models.ForeignKey(
        Diploma,
        on_delete=models.CASCADE,
        related_name="progression_rules",
        verbose_name=_("diploma"),
    )
    semester_pass_percent = models.DecimalField(
        _("semester pass percent"),
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    final_exam_pass_percent = models.DecimalField(
        _("final exam pass percent"),
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    require_all_semesters_passed = models.BooleanField(_("require all semesters passed"), default=True)
    require_final_exam = models.BooleanField(_("require final exam"), default=True)
    auto_promote_enabled = models.BooleanField(_("auto promote enabled"), default=False)
    allow_admin_override = models.BooleanField(_("allow admin override"), default=True)
    allow_retake = models.BooleanField(_("allow retake"), default=True)
    max_retake_count = models.PositiveIntegerField(_("max retake count"), default=0)
    is_active = models.BooleanField(_("active"), default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_progression_rules",
        verbose_name=_("created by"),
    )

    class Meta:
        verbose_name = _("diploma progression rule")
        verbose_name_plural = _("diploma progression rules")
        constraints = [
            models.UniqueConstraint(
                fields=("diploma",),
                condition=models.Q(is_active=True),
                name="unique_active_rule_per_diploma",
            ),
        ]

    def __str__(self):
        return f"Rules: {self.diploma.title}"

    def clean(self):
        if self.final_exam_pass_percent < 0 or self.final_exam_pass_percent > 100:
            raise ValidationError(_("Final exam pass percent must be between 0 and 100."))
        if self.semester_pass_percent < 0 or self.semester_pass_percent > 100:
            raise ValidationError(_("Semester pass percent must be between 0 and 100."))
        if self.require_final_exam and not self.require_all_semesters_passed:
            raise ValidationError(
                _("Final exam requirement cannot be enabled without requiring all semesters.")
            )


class SemesterProgressionRule(TimeStampedModel):
    diploma_rule = models.ForeignKey(
        DiplomaProgressionRule,
        on_delete=models.CASCADE,
        related_name="semester_rules",
        verbose_name=_("diploma progression rule"),
    )
    semester = models.ForeignKey(
        Semester,
        on_delete=models.CASCADE,
        related_name="progression_rules",
        verbose_name=_("semester"),
    )
    pass_percent = models.DecimalField(
        _("pass percent"),
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    require_all_required_assessments = models.BooleanField(
        _("require all required assessments"), default=True,
    )
    allow_missing_optional_assessments = models.BooleanField(
        _("allow missing optional assessments"), default=False,
    )
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("semester progression rule")
        verbose_name_plural = _("semester progression rules")
        unique_together = (("diploma_rule", "semester"),)

    def __str__(self):
        return f"{self.semester} rule"

    def clean(self):
        if self.semester.diploma_id != self.diploma_rule.diploma_id:
            raise ValidationError(_("Semester must belong to the same diploma as the rule."))


class StudentSemesterProgress(TimeStampedModel):
    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="semester_progress",
        verbose_name=_("student"),
    )
    diploma_enrollment = models.ForeignKey(
        "academics.DiplomaEnrollment",
        on_delete=models.CASCADE,
        related_name="semester_progress",
        verbose_name=_("diploma enrollment"),
    )
    cohort = models.ForeignKey(
        Cohort,
        on_delete=models.CASCADE,
        related_name="semester_progress",
        verbose_name=_("cohort"),
    )
    semester = models.ForeignKey(
        Semester,
        on_delete=models.CASCADE,
        related_name="student_progress",
        verbose_name=_("semester"),
    )
    status = models.CharField(
        _("status"),
        max_length=25,
        choices=SEMESTER_PROGRESS_CHOICES,
        default=SEMESTER_PROGRESS_NOT_STARTED,
        db_index=True,
    )
    calculated_score = models.DecimalField(
        _("calculated score"),
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    calculated_at = models.DateTimeField(_("calculated at"), null=True, blank=True)
    passed_at = models.DateTimeField(_("passed at"), null=True, blank=True)
    failed_at = models.DateTimeField(_("failed at"), null=True, blank=True)
    promoted_at = models.DateTimeField(_("promoted at"), null=True, blank=True)
    demoted_at = models.DateTimeField(_("demoted at"), null=True, blank=True)
    locked_at = models.DateTimeField(_("locked at"), null=True, blank=True)
    lock_reason = models.TextField(_("lock reason"), blank=True)
    retake_required = models.BooleanField(_("retake required"), default=False)
    retake_count = models.PositiveIntegerField(_("retake count"), default=0)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="decided_progress",
        verbose_name=_("decided by"),
    )
    decision_reason = models.TextField(_("decision reason"), blank=True)

    class Meta:
        verbose_name = _("student semester progress")
        verbose_name_plural = _("student semester progress")
        constraints = [
            models.UniqueConstraint(
                fields=("diploma_enrollment", "semester"),
                name="unique_progress_per_enrollment_semester",
            ),
        ]
        indexes = [
            models.Index(fields=["student"]),
            models.Index(fields=["cohort"]),
            models.Index(fields=["semester"]),
            models.Index(fields=["status"]),
            models.Index(fields=["promoted_at"]),
            models.Index(fields=["locked_at"]),
        ]

    def __str__(self):
        return f"{self.student} - {self.semester} ({self.status})"


class PromotionDecision(TimeStampedModel):
    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="promotion_decisions",
        verbose_name=_("student"),
    )
    diploma_enrollment = models.ForeignKey(
        "academics.DiplomaEnrollment",
        on_delete=models.CASCADE,
        related_name="promotion_decisions",
        verbose_name=_("diploma enrollment"),
    )
    from_semester = models.ForeignKey(
        Semester,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="from_decisions",
        verbose_name=_("from semester"),
    )
    to_semester = models.ForeignKey(
        Semester,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="to_decisions",
        verbose_name=_("to semester"),
    )
    decision_type = models.CharField(
        _("decision type"),
        max_length=25,
        choices=PROMOTION_DECISION_CHOICES,
        db_index=True,
    )
    previous_status = models.CharField(
        _("previous status"), max_length=25, blank=True,
    )
    new_status = models.CharField(
        _("new status"), max_length=25, blank=True,
    )
    automatic = models.BooleanField(_("automatic"), default=False)
    reason = models.TextField(_("reason"), blank=True)
    score_snapshot = models.DecimalField(
        _("score snapshot"),
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="promotion_decisions",
        verbose_name=_("decided by"),
    )
    decided_at = models.DateTimeField(_("decided at"), auto_now_add=True, db_index=True)
    metadata = models.JSONField(_("metadata"), null=True, blank=True)

    class Meta:
        verbose_name = _("promotion decision")
        verbose_name_plural = _("promotion decisions")
        ordering = ("-decided_at",)
        indexes = [
            models.Index(fields=["student"]),
            models.Index(fields=["diploma_enrollment"]),
            models.Index(fields=["decision_type"]),
        ]

    def __str__(self):
        return f"{self.student} - {self.decision_type} ({self.decided_at})"


class FinalExamEligibility(TimeStampedModel):
    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="final_exam_eligibilities",
        verbose_name=_("student"),
    )
    diploma_enrollment = models.ForeignKey(
        "academics.DiplomaEnrollment",
        on_delete=models.CASCADE,
        related_name="final_exam_eligibilities",
        verbose_name=_("diploma enrollment"),
    )
    eligible = models.BooleanField(_("eligible"), default=False)
    eligible_at = models.DateTimeField(_("eligible at"), null=True, blank=True)
    blocked_reason = models.TextField(_("blocked reason"), blank=True)
    final_assessment = models.ForeignKey(
        "assessments.Assessment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="final_exam_eligibilities",
        verbose_name=_("final assessment"),
    )
    final_attempt = models.ForeignKey(
        "assessments.AssessmentAttempt",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="final_exam_eligibility",
        verbose_name=_("final attempt"),
    )
    passed_final_exam = models.BooleanField(_("passed final exam"), null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_final_exams",
        verbose_name=_("reviewed by"),
    )
    reviewed_at = models.DateTimeField(_("reviewed at"), null=True, blank=True)

    class Meta:
        verbose_name = _("final exam eligibility")
        verbose_name_plural = _("final exam eligibilities")
        constraints = [
            models.UniqueConstraint(
                fields=("diploma_enrollment",),
                name="unique_final_exam_eligibility_per_enrollment",
            ),
        ]

    def __str__(self):
        return f"{self.student} - {'Eligible' if self.eligible else 'Blocked'}"


class StudentDiplomaCompletion(TimeStampedModel):
    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="diploma_completions",
        verbose_name=_("student"),
    )
    diploma_enrollment = models.ForeignKey(
        "academics.DiplomaEnrollment",
        on_delete=models.CASCADE,
        related_name="completions",
        verbose_name=_("diploma enrollment"),
    )
    status = models.CharField(
        _("status"),
        max_length=25,
        choices=DIPLOMA_COMPLETION_CHOICES,
        default=DIPLOMA_COMPLETION_ACTIVE,
        db_index=True,
    )
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)
    graduated_at = models.DateTimeField(_("graduated at"), null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="completed_students",
        verbose_name=_("completed by"),
    )
    graduation_notes = models.TextField(_("graduation notes"), blank=True)
    final_score = models.DecimalField(
        _("final score"),
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    final_exam_attempt = models.ForeignKey(
        "assessments.AssessmentAttempt",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="diploma_completion",
        verbose_name=_("final exam attempt"),
    )

    class Meta:
        verbose_name = _("student diploma completion")
        verbose_name_plural = _("student diploma completions")
        constraints = [
            models.UniqueConstraint(
                fields=("diploma_enrollment",),
                name="unique_completion_per_enrollment",
            ),
        ]

    def __str__(self):
        return f"{self.student} - {self.status}"

    def clean(self):
        if self.final_score is not None and (self.final_score < 0 or self.final_score > 100):
            raise ValidationError(_("Final score must be between 0 and 100."))
