
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.academics.models import Cohort, Diploma, Semester
from apps.core.models import TimeStampedModel
from apps.students.models import StudentProfile

from .constants import (
    COMPONENT_TYPE_CHOICES,
    DIPLOMA_COMPLETION_ACTIVE,
    DIPLOMA_COMPLETION_CHOICES,
    EVALUATION_CONFIG_STATUS_ACTIVE,
    EVALUATION_CONFIG_STATUS_CHOICES,
    FINAL_PROGRESSION_STATUS_CHOICES,
    FINAL_PROGRESSION_STATUS_PENDING,
    INTERVIEW_DECISION_CHOICES,
    INTERVIEW_STATUS_CHOICES,
    INTERVIEW_STATUS_REQUESTED,
    PROMOTION_DECISION_CHOICES,
    REJOIN_STATUS_CHOICES,
    REJOIN_STATUS_PLANNED,
    SEMESTER_EVALUATION_CHOICES,
    SEMESTER_EVALUATION_PASS,
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
    # Retained for historical rule records; active progression no longer uses
    # a separate final-exam gate.
    require_final_exam = models.BooleanField(_("require final exam"), default=False)
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
            # S2-18: durable range enforcement so forged POST data or a save()
            # that skips full_clean() cannot store out-of-range policy.
            models.CheckConstraint(
                condition=models.Q(semester_pass_percent__gte=0) & models.Q(semester_pass_percent__lte=100),
                name="check_diploma_rule_semester_pass_percent_range",
            ),
            models.CheckConstraint(
                condition=models.Q(final_exam_pass_percent__gte=0) & models.Q(final_exam_pass_percent__lte=100),
                name="check_diploma_rule_final_exam_pass_percent_range",
            ),
            models.CheckConstraint(
                condition=models.Q(max_retake_count__gte=0),
                name="check_diploma_rule_max_retake_count_non_negative",
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


class CohortAcademicYear(TimeStampedModel):
    """A configured academic-year boundary for a cohort."""

    cohort = models.ForeignKey(
        Cohort, on_delete=models.CASCADE, related_name="academic_years",
        verbose_name=_("cohort"),
    )
    year_number = models.PositiveIntegerField(verbose_name=_("year number"))
    start_date = models.DateField(verbose_name=_("start date"))
    end_date = models.DateField(verbose_name=_("end date"))
    semesters_per_year = models.PositiveIntegerField(default=2, verbose_name=_("semesters per year"))

    class Meta:
        ordering = ("cohort", "year_number")
        constraints = [
            models.UniqueConstraint(fields=("cohort", "year_number"), name="uq_cohort_academic_year"),
        ]

    def clean(self):
        if self.end_date < self.start_date:
            raise ValidationError(_("Academic year end date must be after its start date."))
        if self.semesters_per_year < 1:
            raise ValidationError(_("Semesters per year must be positive."))


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
        constraints = [
            # S2-18: durable range enforcement for the semester pass threshold.
            models.CheckConstraint(
                condition=models.Q(pass_percent__gte=0) & models.Q(pass_percent__lte=100),
                name="check_semester_rule_pass_percent_range",
            ),
        ]

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
        max_length=30,
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
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
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


class SemesterGradeComponent(TimeStampedModel):
    cohort = models.ForeignKey(
        Cohort,
        on_delete=models.CASCADE,
        related_name="grade_components",
        verbose_name=_("cohort"),
    )
    semester = models.ForeignKey(
        Semester,
        on_delete=models.CASCADE,
        related_name="grade_components",
        verbose_name=_("semester"),
    )
    name = models.CharField(_("name"), max_length=255)
    component_type = models.CharField(
        _("component type"),
        max_length=30,
        choices=COMPONENT_TYPE_CHOICES,
    )
    weight_percent = models.DecimalField(
        _("weight percent"),
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    required_min_percent = models.DecimalField(
        _("required min percent"),
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    required_to_pass = models.BooleanField(_("required to pass"), default=True)
    order = models.PositiveIntegerField(_("order"))
    active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("semester grade component")
        verbose_name_plural = _("semester grade components")
        constraints = [
            models.UniqueConstraint(
                fields=("cohort", "semester", "component_type", "name"),
                name="unique_grade_component_per_cohort_semester_type_name",
            ),
            models.UniqueConstraint(
                fields=("cohort", "semester", "order"),
                name="unique_grade_component_order_per_cohort_semester",
            ),
        ]

    def __str__(self):
        return f"{self.cohort} - {self.semester} - {self.name}"

    def clean(self):
        if self.semester.diploma_id != self.cohort.diploma_id:
            raise ValidationError(
                _("Semester must belong to the same diploma as the cohort.")
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class StudentGradeItem(TimeStampedModel):
    component = models.ForeignKey(
        SemesterGradeComponent,
        on_delete=models.CASCADE,
        related_name="grade_items",
        verbose_name=_("component"),
    )
    diploma_enrollment = models.ForeignKey(
        "academics.DiplomaEnrollment",
        on_delete=models.CASCADE,
        related_name="grade_items",
        verbose_name=_("diploma enrollment"),
    )
    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        verbose_name=_("student"),
    )
    source_assessment_attempt = models.ForeignKey(
        "assessments.AssessmentAttempt",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("source assessment attempt"),
    )
    source_exam_code = models.ForeignKey(
        "assessments.SemesterExamCode",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("source exam code"),
    )
    source_attendance_summary = models.JSONField(
        _("source attendance summary"),
        default=dict,
        blank=True,
    )
    manual_score = models.DecimalField(
        _("manual score"),
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
    )
    max_score = models.DecimalField(
        _("max score"),
        max_digits=7,
        decimal_places=2,
    )
    score_percent = models.DecimalField(
        _("score percent"),
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    visible_to_student = models.BooleanField(_("visible to student"), default=False)
    notes = models.TextField(_("notes"), blank=True)
    graded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("graded by"),
    )
    graded_at = models.DateTimeField(_("graded at"), null=True, blank=True)

    class Meta:
        verbose_name = _("student grade item")
        verbose_name_plural = _("student grade items")
        constraints = [
            models.UniqueConstraint(
                fields=("component", "diploma_enrollment"),
                name="unique_grade_item_per_component_enrollment",
            ),
        ]

    def __str__(self):
        return f"{self.student} - {self.component}"

    def clean(self):
        if self.component.cohort_id != self.diploma_enrollment.cohort_id:
            raise ValidationError(
                _("Component cohort must match diploma enrollment cohort.")
            )
        if self.manual_score is not None and self.manual_score > self.max_score:
            raise ValidationError(_("Manual score cannot exceed max score."))
        if self.score_percent is not None and (self.score_percent < 0 or self.score_percent > 100):
            raise ValidationError(_("Score percent must be between 0 and 100."))

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class SemesterEvaluationConfig(TimeStampedModel):
    """Exact Cohort+Semester evaluation configuration (P2-05).

    Exactly three components (Attendance, Homework, Semester Exam) with
    weights that must sum to 100, plus the three component minimums and one
    overall minimum. ``incomplete`` configs need Admin remediation and block
    finalization. Legacy generic components remain read-only history.
    """

    cohort = models.ForeignKey(
        Cohort,
        on_delete=models.CASCADE,
        related_name="evaluation_configs",
        verbose_name=_("cohort"),
    )
    semester = models.ForeignKey(
        Semester,
        on_delete=models.CASCADE,
        related_name="evaluation_configs",
        verbose_name=_("semester"),
    )
    attendance_weight_percent = models.DecimalField(
        _("attendance weight percent"),
        max_digits=5, decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    homework_weight_percent = models.DecimalField(
        _("homework weight percent"),
        max_digits=5, decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    semester_exam_weight_percent = models.DecimalField(
        _("semester exam weight percent"),
        max_digits=5, decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    attendance_min_percent = models.DecimalField(
        _("attendance min percent"),
        max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    attendance_required_sessions = models.PositiveIntegerField(
        _("required attendance sessions"),
        default=0,
        help_text=_(
            "Required number of attendance records per student for this semester. "
            "The evaluation computes recorded attendances out of this number; "
            "0 uses the number of ended/closed sessions instead."
        ),
    )
    homework_min_percent = models.DecimalField(
        _("homework min percent"),
        max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    semester_exam_min_percent = models.DecimalField(
        _("semester exam min percent"),
        max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    overall_min_percent = models.DecimalField(
        _("overall min percent"),
        max_digits=5, decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=EVALUATION_CONFIG_STATUS_CHOICES,
        default=EVALUATION_CONFIG_STATUS_ACTIVE,
        db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_evaluation_configs",
        verbose_name=_("created by"),
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_evaluation_configs",
        verbose_name=_("updated by"),
    )

    class Meta:
        verbose_name = _("semester evaluation configuration")
        verbose_name_plural = _("semester evaluation configurations")
        constraints = [
            models.UniqueConstraint(
                fields=("cohort", "semester"),
                name="unique_evaluation_config_per_cohort_semester",
            ),
        ]

    def __str__(self):
        return f"{self.cohort} - {self.semester} ({self.get_status_display()})"

    def clean(self):
        if self.semester.diploma_id != self.cohort.diploma_id:
            raise ValidationError(_("Semester must belong to the same diploma as the cohort."))
        if self.status == EVALUATION_CONFIG_STATUS_ACTIVE:
            total = (
                self.attendance_weight_percent
                + self.homework_weight_percent
                + self.semester_exam_weight_percent
            )
            if total != 100:
                raise ValidationError(_("Active evaluation weights must sum to 100."))
            if self.overall_min_percent <= 0:
                raise ValidationError(_("Overall minimum must be positive."))


class SemesterEvaluation(TimeStampedModel):
    """Immutable calculated Semester Evaluation snapshot (P2-05).

    Created once by the finalization service (P2-06) and never mutated.
    Component/config/source snapshots preserve the exact inputs used at
    finalization time.
    """

    diploma_enrollment = models.ForeignKey(
        "academics.DiplomaEnrollment",
        on_delete=models.CASCADE,
        related_name="semester_evaluations",
        verbose_name=_("diploma enrollment"),
    )
    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="semester_evaluations",
        verbose_name=_("student"),
    )
    cohort = models.ForeignKey(
        Cohort,
        on_delete=models.CASCADE,
        related_name="semester_evaluations",
        verbose_name=_("cohort"),
    )
    semester = models.ForeignKey(
        Semester,
        on_delete=models.CASCADE,
        related_name="semester_evaluations",
        verbose_name=_("semester"),
    )
    result = models.CharField(
        _("result"),
        max_length=10,
        choices=SEMESTER_EVALUATION_CHOICES,
        default=SEMESTER_EVALUATION_PASS,
    )
    overall_percent = models.DecimalField(
        _("overall percent"),
        max_digits=5, decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    attendance_percent = models.DecimalField(
        _("attendance percent"),
        max_digits=5, decimal_places=2, null=True, blank=True,
    )
    homework_percent = models.DecimalField(
        _("homework percent"),
        max_digits=5, decimal_places=2, null=True, blank=True,
    )
    semester_exam_percent = models.DecimalField(
        _("semester exam percent"),
        max_digits=5, decimal_places=2, null=True, blank=True,
    )
    config_snapshot = models.JSONField(_("config snapshot"), default=dict, blank=True)
    source_snapshot = models.JSONField(_("source snapshot"), default=dict, blank=True)
    finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="finalized_semester_evaluations",
        verbose_name=_("finalized by"),
    )
    finalized_at = models.DateTimeField(_("finalized at"), auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _("semester evaluation")
        verbose_name_plural = _("semester evaluations")
        constraints = [
            models.UniqueConstraint(
                fields=("diploma_enrollment", "semester"),
                name="unique_semester_evaluation_per_enrollment_semester",
            ),
            models.CheckConstraint(
                condition=models.Q(overall_percent__gte=0) & models.Q(overall_percent__lte=100),
                name="check_semester_evaluation_overall_percent_range",
            ),
        ]
        indexes = [
            models.Index(fields=["student"]),
            models.Index(fields=["cohort"]),
            models.Index(fields=["semester"]),
        ]

    def __str__(self):
        return f"{self.student} - {self.semester} ({self.get_result_display()})"


class FinalProgressionOutcome(TimeStampedModel):
    """Authoritative final progression outcome (P2-07).

    Tied one-to-one to the immutable ``SemesterEvaluation``. Holds the
    calculated result snapshot and the authoritative final decision
    (Continue/Promote or Do Not Continue). ``interview_pending`` blocks all
    final resolution until the linked interview is completed.
    """

    evaluation = models.OneToOneField(
        SemesterEvaluation,
        on_delete=models.CASCADE,
        related_name="progression_outcome",
        verbose_name=_("semester evaluation"),
    )
    diploma_enrollment = models.ForeignKey(
        "academics.DiplomaEnrollment",
        on_delete=models.CASCADE,
        related_name="progression_outcomes",
        verbose_name=_("diploma enrollment"),
    )
    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="progression_outcomes",
        verbose_name=_("student"),
    )
    semester = models.ForeignKey(
        Semester,
        on_delete=models.CASCADE,
        related_name="progression_outcomes",
        verbose_name=_("semester"),
    )
    calculated_result = models.CharField(
        _("calculated result"),
        max_length=10,
        choices=SEMESTER_EVALUATION_CHOICES,
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=FINAL_PROGRESSION_STATUS_CHOICES,
        default=FINAL_PROGRESSION_STATUS_PENDING,
        db_index=True,
    )
    final_result = models.CharField(
        _("final result"),
        max_length=10,
        choices=SEMESTER_EVALUATION_CHOICES,
        blank=True,
        default="",
    )
    is_exceptional = models.BooleanField(_("exceptional decision"), null=True, blank=True)
    reason = models.TextField(_("reason"), blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="decided_progression_outcomes",
        verbose_name=_("decided by"),
    )
    decided_at = models.DateTimeField(_("decided at"), null=True, blank=True)

    class Meta:
        verbose_name = _("final progression outcome")
        verbose_name_plural = _("final progression outcomes")
        indexes = [
            models.Index(fields=["student"]),
            models.Index(fields=["semester"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.student} - {self.semester} ({self.get_status_display()})"


class SemesterInterview(TimeStampedModel):
    """One post-evaluation interview (P2-07).

    Requested after a finalized evaluation (PASS or FAIL); while requested it
    blocks final resolution. Completion records the structured outcome and may
    be followed by either final direction.
    """

    outcome = models.OneToOneField(
        FinalProgressionOutcome,
        on_delete=models.CASCADE,
        related_name="interview",
        verbose_name=_("final progression outcome"),
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=INTERVIEW_STATUS_CHOICES,
        default=INTERVIEW_STATUS_REQUESTED,
        db_index=True,
    )
    outcome_result = models.CharField(
        _("interview outcome"),
        max_length=10,
        choices=INTERVIEW_DECISION_CHOICES,
        blank=True,
        default="",
    )
    details = models.TextField(_("details"), blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_semester_interviews",
        verbose_name=_("requested by"),
    )
    requested_at = models.DateTimeField(_("requested at"), auto_now_add=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="completed_semester_interviews",
        verbose_name=_("completed by"),
    )
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)

    class Meta:
        verbose_name = _("semester interview")
        verbose_name_plural = _("semester interviews")

    def __str__(self):
        return f"Interview {self.get_status_display()} for {self.outcome}"


class SemesterRejoinPlan(TimeStampedModel):
    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="rejoin_plans",
        verbose_name=_("student"),
    )
    from_enrollment = models.ForeignKey(
        "academics.DiplomaEnrollment",
        on_delete=models.CASCADE,
        related_name="rejoin_from",
        verbose_name=_("from enrollment"),
    )
    to_enrollment = models.ForeignKey(
        "academics.DiplomaEnrollment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rejoin_to",
        verbose_name=_("to enrollment"),
    )
    failed_semester = models.ForeignKey(
        "academics.Semester",
        on_delete=models.CASCADE,
        related_name="rejoin_plans",
        verbose_name=_("failed semester"),
    )
    target_cohort = models.ForeignKey(
        "academics.Cohort",
        on_delete=models.CASCADE,
        related_name="rejoin_plans",
        verbose_name=_("target cohort"),
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=REJOIN_STATUS_CHOICES,
        default=REJOIN_STATUS_PLANNED,
        db_index=True,
    )
    planned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="planned_rejoins",
        verbose_name=_("planned by"),
    )
    activated_at = models.DateTimeField(null=True, blank=True, verbose_name=_("activated at"))
    notes = models.TextField(blank=True, verbose_name=_("notes"))

    class Meta:
        verbose_name = _("semester rejoin plan")
        verbose_name_plural = _("semester rejoin plans")
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["student"],
                condition=models.Q(status__in=[REJOIN_STATUS_PLANNED, "active"]),
                name="uq_active_rejoin_plan_per_student",
            ),
        ]

    def __str__(self):
        return f"{self.student} - {self.failed_semester} ({self.status})"
