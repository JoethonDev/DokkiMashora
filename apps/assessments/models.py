from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.academics.models import (
    Cohort,
    CourseVersion,
    DiplomaEnrollment,
    DiplomaLecture,
    Semester,
)
from apps.core.models import TimeStampedModel

from .constants import (
    ASSESSMENT_TYPE_CHOICES,
    ASSESSMENT_TYPE_HOMEWORK,
    ASSESSMENT_TYPE_QUIZ,
    ASSESSMENT_TYPE_SEMESTER_EXAM,
    ATTEMPT_STATUS_ASSIGNED,
    ATTEMPT_STATUS_CHOICES,
    DELIVERY_MODE_CHOICES,
    DELIVERY_MODE_ONLINE,
    EXAM_OVERRIDE_ACTIVE,
    EXAM_OVERRIDE_STATUS_CHOICES,
    EXAM_REVIEW_STATUS_CHOICES,
    EXAM_REVIEW_STATUS_NOT_REVIEWED,
    GRADE_VISIBILITY_CHOICES,
    GRADE_VISIBILITY_HIDDEN,
    HOMEWORK_STATUS_CHOICES,
    HOMEWORK_STATUS_UPLOADED,
    LEGACY_STATUS_ACTIVE,
    LEGACY_STATUS_CHOICES,
    QUESTION_TYPE_CHOICES,
    QUESTION_TYPE_MCQ,
    QUESTION_TYPE_WRITTEN,
    RELEASE_MODE_CHOICES,
    RELEASE_MODE_MANUAL,
    REVIEWER_ACCESS_BOTH,
    REVIEWER_ACCESS_CHOICES,
    REVIEWER_CLAIM_IN_PROGRESS,
    REVIEWER_CLAIM_STATUS_CHOICES,
    REVIEWER_SCOPE_CHOICES,
    REVIEWER_SCOPE_COHORT,
    REVIEWER_SCOPE_EXAM,
    REVIEWER_SCOPE_SEMESTER,
    SNAPSHOT_QUESTION_SOURCE_CHOICES,
    SUBMISSION_STATUS_CHOICES,
    SUBMISSION_STATUS_FAILED,
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
    is_completion_exam = models.BooleanField(
        _("end-course exam"),
        default=False,
        help_text=_(
            "Passing this course quiz defines course completion. Only one quiz per course version can be the end-course exam."
        ),
    )
    is_required_for_semester_evaluation = models.BooleanField(
        _("Required for semester evaluation"),
        default=True,
        help_text=_(
            "If absent, this assessment may block the semester result depending on the diploma or semester progression policy."
        ),
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
        null=True,
        blank=True,
        help_text=_("For homework/file assessments: maximum number of files a student may upload. Leave blank for no limit."),
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
    release_mode = models.CharField(
        _("release mode"),
        max_length=12,
        choices=RELEASE_MODE_CHOICES,
        default=RELEASE_MODE_MANUAL,
        db_index=True,
        help_text=_("Automatic releases grades as soon as the final grade commits; Manual requires an Admin/Super Admin release action."),
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
    legacy_status = models.CharField(
        _("legacy status"),
        max_length=24,
        choices=LEGACY_STATUS_CHOICES,
        default=LEGACY_STATUS_ACTIVE,
        db_index=True,
        help_text=_("Active assessments participate in workflows; legacy rows are preserved read-only."),
    )
    legacy_note = models.TextField(
        _("legacy note"),
        blank=True,
        help_text=_("Reason a legacy assessment is archived or requires review."),
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
        constraints = [
            # Conditional CHECKs use implication form: legacy (non-active) rows
            # are exempt, active rows must satisfy the invariant. A plain
            # ``legacy_status=active AND invariant`` would be FALSE (violated)
            # for every legacy row because CHECK must hold for all rows.
            models.CheckConstraint(
                condition=models.Q(~models.Q(legacy_status=LEGACY_STATUS_ACTIVE))
                | models.Q(course_version__isnull=False, semester__isnull=True, cohort__isnull=True)
                | models.Q(course_version__isnull=True, semester__isnull=False, cohort__isnull=False),
                name="check_assessment_active_exclusive_scope",
            ),
            models.CheckConstraint(
                condition=models.Q(~models.Q(legacy_status=LEGACY_STATUS_ACTIVE))
                | models.Q(
                    assessment_type=ASSESSMENT_TYPE_QUIZ,
                    course_version__isnull=False, semester__isnull=True, cohort__isnull=True,
                )
                | models.Q(
                    assessment_type=ASSESSMENT_TYPE_HOMEWORK,
                    course_version__isnull=True, semester__isnull=False, cohort__isnull=False,
                )
                | models.Q(
                    assessment_type=ASSESSMENT_TYPE_SEMESTER_EXAM,
                    course_version__isnull=True, semester__isnull=False, cohort__isnull=False,
                ),
                name="check_assessment_active_type_scope",
            ),
            models.CheckConstraint(
                condition=models.Q(~models.Q(legacy_status=LEGACY_STATUS_ACTIVE)) | models.Q(max_attempts=1),
                name="check_assessment_active_one_attempt",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        course_version__isnull=True,
                        semester__isnull=False,
                        cohort__isnull=False,
                        assessment_type__in=(ASSESSMENT_TYPE_HOMEWORK, ASSESSMENT_TYPE_SEMESTER_EXAM),
                    )
                    | models.Q(is_required_for_semester_evaluation=True)
                ),
                name="check_asmt_optional_scope",
            ),
            models.UniqueConstraint(
                fields=("course_version",),
                condition=models.Q(is_completion_exam=True),
                name="uq_assessment_completion_exam_per_course_version",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(is_completion_exam=False)
                    | models.Q(
                        assessment_type=ASSESSMENT_TYPE_QUIZ,
                        course_version__isnull=False,
                    )
                ),
                name="check_assessment_completion_exam_course_quiz",
            ),
        ]

    def __str__(self):
        return self.title

    @property
    def is_legacy(self) -> bool:
        """True when this assessment is archived/review-required historical data."""
        return self.legacy_status != LEGACY_STATUS_ACTIVE

    @property
    def is_course_scoped(self) -> bool:
        return self.course_version_id is not None

    @property
    def is_diploma_scoped(self) -> bool:
        return self.course_version_id is None and bool(self.semester_id and self.cohort_id)

    def clean(self):
        if self.open_at and self.close_at and self.open_at >= self.close_at:
            raise ValidationError(_("Open time must be before close time."))
        if not self.is_required_for_semester_evaluation and not (
            self.course_version_id is None
            and self.semester_id is not None
            and self.cohort_id is not None
            and self.assessment_type in (ASSESSMENT_TYPE_HOMEWORK, ASSESSMENT_TYPE_SEMESTER_EXAM)
        ):
            raise ValidationError(
                _("Only diploma Homework and Semester Exam assessments can be optional for semester evaluation.")
            )
        if self.duration_minutes is not None and self.duration_minutes < 1:
            raise ValidationError(_("Duration must be positive for timed assessments."))
        if self.max_attempts < 1:
            raise ValidationError(_("Max attempts must be at least 1."))
        if self.max_upload_files is not None and self.max_upload_files < 1:
            raise ValidationError(_("Max uploaded files must be at least 1, or left blank for no limit."))
        if self.legacy_status == LEGACY_STATUS_ACTIVE:
            # The locked type/scope matrix applies to active rows only.
            # Legacy/quarantined rows are frozen historical data.
            has_course_scope = self.course_version_id is not None
            has_diploma_scope = self.course_version_id is None and bool(self.semester_id and self.cohort_id)
            if not (has_course_scope ^ has_diploma_scope):
                raise ValidationError(
                    _("An active assessment must have exactly one scope: a course version, or a diploma semester and cohort.")
                )
            if has_course_scope and self.assessment_type != ASSESSMENT_TYPE_QUIZ:
                raise ValidationError(_("Course-scoped assessments can only be a Quiz."))
            if has_diploma_scope and self.assessment_type not in (
                ASSESSMENT_TYPE_HOMEWORK,
                ASSESSMENT_TYPE_SEMESTER_EXAM,
            ):
                raise ValidationError(_("Diploma-scoped assessments can only be Homework or Semester Exam."))
            if self.max_attempts != 1:
                raise ValidationError(_("Active assessments allow exactly one attempt."))


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
    possible_points_snapshot = models.DecimalField(
        _("possible points snapshot"),
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Maximum possible points frozen for this attempt's online questions or offline section layout."),
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
    is_exceptional = models.BooleanField(_("exceptional"), default=False)
    exam_review_status = models.CharField(
        _("exam review status"),
        max_length=20,
        choices=EXAM_REVIEW_STATUS_CHOICES,
        default=EXAM_REVIEW_STATUS_NOT_REVIEWED,
        db_index=True,
        help_text=_("Admin Semester Exam stage decision (P3-03), distinct from grade release."),
    )
    exam_reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_semester_exam_attempts",
        verbose_name=_("exam reviewed by"),
    )
    exam_reviewed_at = models.DateTimeField(_("exam reviewed at"), null=True, blank=True)
    exam_review_note = models.TextField(_("exam review note"), blank=True)

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
            models.Index(fields=["student", "assessment", "status"]),
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


class HomeworkAttachment(TimeStampedModel):
    """One uploaded homework file (attachment history, P2-02).

    Recast from the legacy per-file ``HomeworkSubmission``. Every historical
    file row is preserved here; the authoritative logical submission is
    ``HomeworkSubmission``. Legacy per-file status/score/feedback/attempt
    columns are kept for reconciliation and must never be treated as the
    authoritative grade.
    """

    submission = models.ForeignKey(
        "HomeworkSubmission",
        on_delete=models.CASCADE,
        related_name="attachments",
        verbose_name=_("homework submission"),
        null=True,
        blank=True,
    )
    assessment = models.ForeignKey(
        Assessment,
        on_delete=models.CASCADE,
        related_name="homework_attachments",
        verbose_name=_("assessment"),
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="homework_attachments",
        verbose_name=_("student"),
    )
    attempt = models.ForeignKey(
        AssessmentAttempt,
        on_delete=models.CASCADE,
        related_name="homework_attachments",
        verbose_name=_("legacy attempt"),
        null=True,
        blank=True,
        help_text=_("Legacy per-file attempt created before aggregate submissions."),
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
        help_text=_("Legacy per-file status preserved for reconciliation."),
    )
    submitted_at = models.DateTimeField(_("submitted at"), null=True, blank=True)
    reviewed_at = models.DateTimeField(_("reviewed at"), null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_homework_attachments",
        verbose_name=_("reviewed by"),
    )
    score = models.DecimalField(
        _("legacy score"),
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Legacy per-file score preserved for reconciliation; the authoritative score lives on the aggregate submission."),
    )
    feedback = models.TextField(_("legacy feedback"), blank=True)

    class Meta:
        verbose_name = _("homework attachment")
        verbose_name_plural = _("homework attachments")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["assessment", "student"]),
            models.Index(fields=["submission"]),
        ]

    def __str__(self):
        return f"{self.student} - {self.assessment} - {self.original_filename}"


class HomeworkSubmission(TimeStampedModel):
    """One logical homework submission per assessment+student (P2-02).

    Holds the aggregate lifecycle, the canonical attempt, one aggregate
    score/feedback, and links to every uploaded ``HomeworkAttachment``.
    Legacy review-required aggregates are preserved read-only and never
    carry an authoritative score.
    """

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
        related_name="homework_submission",
        verbose_name=_("attempt"),
        null=True,
        blank=True,
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=HOMEWORK_STATUS_CHOICES,
        default=HOMEWORK_STATUS_UPLOADED,
        db_index=True,
    )
    submitted_at = models.DateTimeField(_("submitted at"), null=True, blank=True)
    score = models.DecimalField(
        _("score"),
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    feedback = models.TextField(_("feedback"), blank=True)
    reviewed_at = models.DateTimeField(_("reviewed at"), null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_homework_submissions",
        verbose_name=_("reviewed by"),
    )
    legacy_status = models.CharField(
        _("legacy status"),
        max_length=24,
        choices=LEGACY_STATUS_CHOICES,
        default=LEGACY_STATUS_ACTIVE,
        db_index=True,
        help_text=_("Active submissions participate in workflows; legacy rows are preserved read-only."),
    )
    legacy_note = models.TextField(_("legacy note"), blank=True)

    class Meta:
        verbose_name = _("homework submission")
        verbose_name_plural = _("homework submissions")
        ordering = ("-created_at",)
        # Explicit table name: the legacy per-file table was renamed to
        # assessments_homeworkattachment; keeping a distinct table here avoids
        # index-name collisions left behind by the SQLite/PostgreSQL rename.
        db_table = "assessments_homework_submission"
        constraints = [
            models.UniqueConstraint(
                fields=["assessment", "student"],
                name="unique_homework_submission_per_student",
            ),
            # Review-required (conflicted/legacy) aggregates must not carry an
            # authoritative score. Expressed as ``active OR score is null`` so
            # the CHECK holds for every row.
            models.CheckConstraint(
                condition=models.Q(legacy_status=LEGACY_STATUS_ACTIVE) | models.Q(score__isnull=True),
                name="check_homework_submission_legacy_ungraded",
            ),
        ]
        indexes = [
            models.Index(fields=["assessment", "status"]),
            models.Index(fields=["student"]),
        ]

    def __str__(self):
        return f"{self.student} - {self.assessment}"


class DiplomaLectureQuestion(TimeStampedModel):
    lecture = models.ForeignKey(DiplomaLecture, on_delete=models.CASCADE, related_name="question_bank", verbose_name=_("lecture"))
    text = models.TextField(_("question text"))
    question_type = models.CharField(_("type"), max_length=15, choices=QUESTION_TYPE_CHOICES, default=QUESTION_TYPE_MCQ)
    points = models.DecimalField(_("points"), max_digits=5, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    active = models.BooleanField(_("active"), default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_diploma_lecture_questions", verbose_name=_("created by"))

    class Meta:
        verbose_name = _("diploma lecture question")
        verbose_name_plural = _("diploma lecture questions")
        indexes = [models.Index(fields=["lecture", "active"])]

    def __str__(self):
        return self.text[:50]


class DiplomaLectureChoice(TimeStampedModel):
    question = models.ForeignKey(DiplomaLectureQuestion, on_delete=models.CASCADE, related_name="choices", verbose_name=_("question"))
    text = models.CharField(_("text"), max_length=500)
    is_correct = models.BooleanField(_("correct"), default=False)
    order = models.PositiveIntegerField(_("order"))

    class Meta:
        verbose_name = _("diploma lecture choice")
        verbose_name_plural = _("diploma lecture choices")
        ordering = ("order",)
        constraints = [
            models.UniqueConstraint(fields=["question", "order"], name="uq_diploma_lecture_choice_order"),
        ]

    def __str__(self):
        return self.text[:50]

    def clean(self):
        if self.question_id:
            q_type = DiplomaLectureQuestion.objects.filter(pk=self.question_id).values_list("question_type", flat=True).first()
            if q_type is None:
                raise ValidationError(_("Referenced question does not exist."))
            if q_type != QUESTION_TYPE_MCQ:
                raise ValidationError(_("Choices are only allowed for MCQ questions."))

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class AssessmentLectureSection(TimeStampedModel):
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name="lecture_sections", verbose_name=_("assessment"))
    lecture = models.ForeignKey(DiplomaLecture, on_delete=models.CASCADE, related_name="assessment_sections", verbose_name=_("diploma lecture"))
    order = models.PositiveIntegerField(_("order"))
    mcq_count = models.PositiveIntegerField(_("MCQ count"), default=0)
    max_grade = models.DecimalField(_("max grade"), max_digits=7, decimal_places=2)

    class Meta:
        verbose_name = _("assessment lecture section")
        verbose_name_plural = _("assessment lecture sections")
        ordering = ("order",)
        constraints = [
            models.UniqueConstraint(fields=["assessment", "lecture"], name="uq_assessment_section_lecture"),
            models.UniqueConstraint(fields=["assessment", "order"], name="uq_assessment_section_order"),
        ]

    def __str__(self):
        return f"{self.assessment} - {self.lecture}"

    def calculated_max_grade(self):
        manual_total = (
            sum(self.manual_questions.values_list("points", flat=True), Decimal("0.00"))
            if self.pk else Decimal("0.00")
        )
        bank_points = list(
            self.lecture.question_bank.filter(
                active=True, question_type=QUESTION_TYPE_MCQ,
            ).order_by("-points", "id").values_list("points", flat=True)[: self.mcq_count]
        )
        return manual_total + sum(bank_points, Decimal("0.00"))

    def clean(self):
        if not self.assessment_id:
            return
        if self.assessment.course_version_id:
            raise ValidationError(_("Lecture sections are only for diploma-scoped assessments."))
        if not self.assessment.semester_id or not self.assessment.cohort_id:
            raise ValidationError(_("Assessment must have semester and cohort for lecture sections."))
        if self.lecture_id:
            if self.lecture.cohort_id != self.assessment.cohort_id:
                raise ValidationError(_("Lecture must belong to the assessment's cohort."))
            if self.lecture.semester_id != self.assessment.semester_id:
                raise ValidationError(_("Lecture must belong to the assessment's semester."))
        if self.mcq_count < 0:
            raise ValidationError(_("MCQ count cannot be negative."))


class AssessmentSectionManualQuestion(TimeStampedModel):
    section = models.ForeignKey(AssessmentLectureSection, on_delete=models.CASCADE, related_name="manual_questions", verbose_name=_("section"))
    text = models.TextField(_("question text"))
    question_type = models.CharField(_("type"), max_length=15, choices=QUESTION_TYPE_CHOICES, default=QUESTION_TYPE_WRITTEN)
    points = models.DecimalField(_("points"), max_digits=5, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    order = models.PositiveIntegerField(_("order"))
    required = models.BooleanField(_("required"), default=True)
    rubric = models.TextField(_("rubric"), blank=True)

    class Meta:
        verbose_name = _("assessment section manual question")
        verbose_name_plural = _("assessment section manual questions")
        ordering = ("order",)

    def __str__(self):
        return self.text[:50]


class AssessmentAttemptQuestion(TimeStampedModel):
    attempt = models.ForeignKey(AssessmentAttempt, on_delete=models.CASCADE, related_name="snapshot_questions", verbose_name=_("attempt"))
    section = models.ForeignKey(AssessmentLectureSection, on_delete=models.CASCADE, related_name="snapshot_questions", verbose_name=_("section"))
    source_lecture = models.ForeignKey(DiplomaLecture, on_delete=models.CASCADE, verbose_name=_("source lecture"))
    source_bank_question = models.ForeignKey(DiplomaLectureQuestion, on_delete=models.SET_NULL, null=True, blank=True, related_name="snapshot_questions", verbose_name=_("source bank question"))
    source_manual_question = models.ForeignKey(AssessmentSectionManualQuestion, on_delete=models.SET_NULL, null=True, blank=True, related_name="snapshot_questions", verbose_name=_("source manual question"))
    source_type = models.CharField(_("source type"), max_length=10, choices=SNAPSHOT_QUESTION_SOURCE_CHOICES)
    text_snapshot = models.TextField(_("question text snapshot"))
    question_type = models.CharField(_("type"), max_length=15, choices=QUESTION_TYPE_CHOICES)
    points = models.DecimalField(_("points"), max_digits=7, decimal_places=2)
    order = models.PositiveIntegerField(_("order"))
    required = models.BooleanField(_("required"), default=True)
    rubric_snapshot = models.TextField(_("rubric snapshot"), blank=True)

    class Meta:
        verbose_name = _("attempt snapshot question")
        verbose_name_plural = _("attempt snapshot questions")
        ordering = ("order",)
        constraints = [
            models.UniqueConstraint(fields=["attempt", "order"], name="uq_attempt_snapshot_question_order"),
        ]

    def __str__(self):
        return f"Q{self.order} for attempt {self.attempt_id}"


class AssessmentAttemptChoice(TimeStampedModel):
    attempt_question = models.ForeignKey(AssessmentAttemptQuestion, on_delete=models.CASCADE, related_name="choices", verbose_name=_("attempt question"))
    text_snapshot = models.CharField(_("text"), max_length=500)
    is_correct_snapshot = models.BooleanField(_("correct"), default=False)
    order = models.PositiveIntegerField(_("order"))

    class Meta:
        verbose_name = _("attempt snapshot choice")
        verbose_name_plural = _("attempt snapshot choices")
        ordering = ("order",)

    def __str__(self):
        return self.text_snapshot[:50]


class AssessmentAttemptSnapshotAnswer(TimeStampedModel):
    attempt_question = models.ForeignKey(AssessmentAttemptQuestion, on_delete=models.CASCADE, related_name="answers", verbose_name=_("attempt question"))
    selected_choice = models.ForeignKey(AssessmentAttemptChoice, on_delete=models.SET_NULL, null=True, blank=True, related_name="answers", verbose_name=_("selected choice"))
    written_text = models.TextField(_("written text"), blank=True)
    score = models.DecimalField(_("score"), max_digits=7, decimal_places=2, null=True, blank=True)
    feedback = models.TextField(_("feedback"), blank=True)
    graded_at = models.DateTimeField(_("graded at"), null=True, blank=True)
    graded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="graded_snapshot_answers", verbose_name=_("graded by"))
    score_override_reason = models.TextField(_("score override reason"), blank=True)

    class Meta:
        verbose_name = _("attempt snapshot answer")
        verbose_name_plural = _("attempt snapshot answers")
        constraints = [
            models.UniqueConstraint(fields=["attempt_question"], name="uq_attempt_snapshot_answer"),
        ]

    def __str__(self):
        return f"Answer to snapshot Q{self.attempt_question.order}"


class AssessmentSubmission(TimeStampedModel):
    """Durable final payload and processing state for an online submission.

    The row is created in the same transaction that closes the attempt.  It is
    therefore sufficient to recover grading even if Redis, Celery, or the web
    process fails immediately after the student receives the response.
    """

    submission_id = models.CharField(
        _("submission id"), max_length=64, unique=True,
    )
    attempt = models.OneToOneField(
        AssessmentAttempt,
        on_delete=models.PROTECT,
        related_name="durable_submission",
        verbose_name=_("attempt"),
    )
    final_payload = models.JSONField(_("final payload"))
    accepted_at = models.DateTimeField(_("accepted at"))
    processing_status = models.CharField(
        _("processing status"),
        max_length=20,
        choices=SUBMISSION_STATUS_CHOICES,
        default="pending",
        db_index=True,
    )
    processing_started_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    retry_count = models.PositiveIntegerField(default=0)
    last_error_code = models.CharField(max_length=80, blank=True)
    last_error_detail = models.TextField(blank=True)

    class Meta:
        verbose_name = _("assessment submission")
        verbose_name_plural = _("assessment submissions")
        indexes = [
            models.Index(fields=["processing_status", "accepted_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(processing_status=SUBMISSION_STATUS_FAILED)
                | models.Q(failed_at__isnull=False),
                name="submission_failed_has_failed_at",
            ),
        ]

    def __str__(self):
        return f"{self.submission_id} - attempt {self.attempt_id}"


class SemesterExamAccessOverride(TimeStampedModel):
    assessment = models.ForeignKey(
        Assessment, on_delete=models.CASCADE,
        related_name="access_overrides", verbose_name=_("assessment"),
    )
    diploma_enrollment = models.ForeignKey(
        "academics.DiplomaEnrollment", on_delete=models.CASCADE,
        related_name="semester_exam_access_overrides", verbose_name=_("diploma enrollment"),
        null=True, blank=True,
    )
    course_enrollment = models.ForeignKey(
        "academics.CourseEnrollment", on_delete=models.CASCADE,
        related_name="course_assessment_access_overrides", verbose_name=_("course enrollment"),
        null=True, blank=True,
    )
    exam_code = models.ForeignKey(
        "SemesterExamCode", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="access_overrides", verbose_name=_("exam code"),
    )
    open_at = models.DateTimeField(_("open at"))
    close_at = models.DateTimeField(_("close at"))
    max_attempts = models.PositiveIntegerField(_("max attempts"), default=1)
    status = models.CharField(
        _("status"), max_length=20,
        choices=EXAM_OVERRIDE_STATUS_CHOICES, default=EXAM_OVERRIDE_ACTIVE,
        db_index=True,
    )
    reason = models.TextField(_("reason"))
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="created_exam_overrides", verbose_name=_("created by"),
    )
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="cancelled_exam_overrides", verbose_name=_("cancelled by"),
    )
    cancelled_at = models.DateTimeField(_("cancelled at"), null=True, blank=True)
    used_count = models.PositiveIntegerField(_("used count"), default=0)
    used_at = models.DateTimeField(_("used at"), null=True, blank=True)
    attempt = models.OneToOneField(
        "AssessmentAttempt", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="exceptional_override", verbose_name=_("attempt"),
    )
    metadata = models.JSONField(_("metadata"), default=dict, blank=True)

    class Meta:
        verbose_name = _("semester exam access override")
        verbose_name_plural = _("semester exam access overrides")
        constraints = [
            models.UniqueConstraint(
                fields=["assessment", "diploma_enrollment"],
                condition=models.Q(status=EXAM_OVERRIDE_ACTIVE, diploma_enrollment__isnull=False),
                name="uq_active_override_per_assessment_diploma",
            ),
            models.UniqueConstraint(
                fields=["assessment", "course_enrollment"],
                condition=models.Q(status=EXAM_OVERRIDE_ACTIVE, course_enrollment__isnull=False),
                name="uq_active_override_per_assessment_course",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(diploma_enrollment__isnull=False, course_enrollment__isnull=True)
                    | models.Q(diploma_enrollment__isnull=True, course_enrollment__isnull=False)
                ),
                name="ck_override_exactly_one_enrollment",
            ),
        ]
        indexes = [
            models.Index(fields=["assessment", "status"]),
            models.Index(fields=["diploma_enrollment", "status"]),
            models.Index(fields=["course_enrollment", "status"]),
            models.Index(fields=["open_at", "close_at"]),
        ]

    def clean(self):
        if self.assessment_id:
            is_course_quiz = self.assessment.assessment_type == ASSESSMENT_TYPE_QUIZ
            is_semester_exam = self.assessment.assessment_type == ASSESSMENT_TYPE_SEMESTER_EXAM
            if not (is_course_quiz or is_semester_exam):
                raise ValidationError(_("Overrides are only allowed for quizzes and semester exams."))
            if is_course_quiz and (self.assessment.course_version_id is None or self.diploma_enrollment_id):
                raise ValidationError(_("Quiz overrides require a course enrollment."))
            if is_semester_exam and (
                self.assessment.course_version_id is not None
                or self.assessment.cohort_id is None
                or self.assessment.semester_id is None
                or self.course_enrollment_id
            ):
                raise ValidationError(_("Semester exam overrides require a diploma enrollment."))
            if (is_course_quiz and not self.course_enrollment_id) or (is_semester_exam and not self.diploma_enrollment_id):
                raise ValidationError(_("An enrollment is required for exceptional access."))
        if bool(self.diploma_enrollment_id) == bool(self.course_enrollment_id):
            raise ValidationError(_("Exceptional access must target exactly one enrollment."))
        if self.diploma_enrollment_id and self.assessment_id:
            if self.diploma_enrollment.cohort_id != self.assessment.cohort_id:
                raise ValidationError(_("Enrollment cohort must match the assessment cohort."))
        if self.course_enrollment_id and self.assessment_id:
            if self.course_enrollment.course_version_id != self.assessment.course_version_id:
                raise ValidationError(_("Enrollment course must match the assessment course."))
        if self.open_at and self.close_at and self.open_at >= self.close_at:
            raise ValidationError(_("open_at must be before close_at."))
        if self.max_attempts is not None and self.max_attempts < 1:
            raise ValidationError(_("max_attempts must be at least 1."))
        if not self.reason:
            raise ValidationError(_("Reason is required."))
        if self.course_enrollment_id and self.exam_code_id:
            raise ValidationError(_("Course quiz overrides cannot use an exam code."))
        if self.exam_code_id and self.assessment_id:
            if self.exam_code.assessment_id != self.assessment_id or self.exam_code.diploma_enrollment_id != self.diploma_enrollment_id:
                raise ValidationError(_("Exam code must belong to the same assessment and enrollment."))

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        enrollment_id = self.diploma_enrollment_id or self.course_enrollment_id
        return f"Override {self.assessment_id} - {enrollment_id} ({self.status})"


class SemesterExamCode(TimeStampedModel):
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name="exam_codes", verbose_name=_("assessment"))
    diploma_enrollment = models.ForeignKey(DiplomaEnrollment, on_delete=models.CASCADE, related_name="semester_exam_codes", verbose_name=_("diploma enrollment"))
    code_number = models.CharField(_("code number"), max_length=20, db_index=True)
    delivery_mode = models.CharField(_("delivery mode"), max_length=10, choices=DELIVERY_MODE_CHOICES, default=DELIVERY_MODE_ONLINE)
    printed_at = models.DateTimeField(_("printed at"), null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_exam_codes", verbose_name=_("created by"))
    active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("semester exam code")
        verbose_name_plural = _("semester exam codes")
        constraints = [
            models.UniqueConstraint(fields=["assessment", "diploma_enrollment"], name="uq_exam_code_enrollment"),
            models.UniqueConstraint(fields=["assessment", "code_number"], name="uq_exam_code_number"),
        ]
        indexes = [
            models.Index(fields=["assessment", "delivery_mode", "active"]),
        ]

    def __str__(self):
        return f"{self.code_number} - {self.assessment}"


class SemesterExamCodeSectionMaximum(TimeStampedModel):
    """Freeze the section identity and maximum used for one issued exam code."""

    exam_code = models.ForeignKey(
        SemesterExamCode,
        on_delete=models.CASCADE,
        related_name="section_maximums",
        verbose_name=_("exam code"),
    )
    section = models.ForeignKey(
        AssessmentLectureSection,
        on_delete=models.RESTRICT,
        related_name="exam_code_maximum_snapshots",
        verbose_name=_("assessment section"),
    )
    order_snapshot = models.PositiveIntegerField(_("section order"))
    section_title_snapshot = models.CharField(_("section title"), max_length=255)
    max_grade_snapshot = models.DecimalField(_("maximum grade"), max_digits=7, decimal_places=2)

    class Meta:
        verbose_name = _("exam code section maximum")
        verbose_name_plural = _("exam code section maximums")
        ordering = ("order_snapshot", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=("exam_code", "section"),
                name="uq_exam_code_section_maximum",
            ),
        ]

    def __str__(self):
        return f"{self.exam_code.code_number} - {self.section_title_snapshot}"


class OfflineSectionGrade(TimeStampedModel):
    exam_code = models.ForeignKey(SemesterExamCode, on_delete=models.CASCADE, related_name="section_grades", verbose_name=_("exam code"))
    section = models.ForeignKey(AssessmentLectureSection, on_delete=models.CASCADE, related_name="offline_grades", verbose_name=_("section"))
    score = models.DecimalField(_("score"), max_digits=7, decimal_places=2)
    feedback = models.TextField(_("feedback"), blank=True)
    graded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="offline_section_grades", verbose_name=_("graded by"))
    graded_at = models.DateTimeField(_("graded at"), auto_now_add=True)

    class Meta:
        verbose_name = _("offline section grade")
        verbose_name_plural = _("offline section grades")
        constraints = [
            models.UniqueConstraint(fields=["exam_code", "section"], name="uq_offline_section_grade"),
        ]

    def clean(self):
        if not self.section_id or self.score is None:
            return
        max_grade = SemesterExamCodeSectionMaximum.objects.filter(
            exam_code_id=self.exam_code_id,
            section_id=self.section_id,
        ).values_list("max_grade_snapshot", flat=True).first()
        if max_grade is None:
            max_grade = self.section.max_grade
        if self.score > max_grade:
            raise ValidationError(_("Score cannot exceed section max grade of %(max)s.") % {"max": max_grade})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.exam_code.code_number} - {self.section}"


class ReviewerAssignment(TimeStampedModel):
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviewer_assignments", verbose_name=_("reviewer"))
    cohort = models.ForeignKey("academics.Cohort", on_delete=models.CASCADE, related_name="reviewer_assignments", verbose_name=_("cohort"))
    semester = models.ForeignKey("academics.Semester", on_delete=models.CASCADE, null=True, blank=True, related_name="reviewer_assignments", verbose_name=_("semester"))
    assessment = models.ForeignKey(
        Assessment, on_delete=models.PROTECT, null=True, blank=True,
        related_name="reviewer_assignments", verbose_name=_("semester exam"),
    )
    scope = models.CharField(
        _("scope"), max_length=20, choices=REVIEWER_SCOPE_CHOICES,
        default=REVIEWER_SCOPE_SEMESTER,
    )
    section = models.ForeignKey("AssessmentLectureSection", on_delete=models.CASCADE, null=True, blank=True, related_name="reviewer_assignments", verbose_name=_("section"))
    cluster = models.ForeignKey(
        "academics.StudentCluster", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reviewer_assignments", verbose_name=_("cluster"),
    )
    access_mode = models.CharField(
        _("access mode"), max_length=10, choices=REVIEWER_ACCESS_CHOICES,
        default=REVIEWER_ACCESS_BOTH,
    )
    active = models.BooleanField(_("active"), default=True)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_reviewer_assignments", verbose_name=_("assigned by"))

    class Meta:
        verbose_name = _("reviewer assignment")
        verbose_name_plural = _("reviewer assignments")
        constraints = [
            models.UniqueConstraint(
                fields=["reviewer", "cohort", "scope", "semester", "assessment", "cluster"],
                condition=models.Q(active=True, section__isnull=True),
                nulls_distinct=False,
                name="uq_active_reviewer_assignment_scope",
            ),
        ]

    def clean(self):
        if self.cluster_id and self.cluster.cohort_id != self.cohort_id:
            raise ValidationError(_("Cluster must belong to the assignment cohort."))
        if self.scope == REVIEWER_SCOPE_COHORT:
            if self.semester_id or self.assessment_id:
                raise ValidationError(_("A cohort assignment cannot include a semester or exam."))
        elif self.scope == REVIEWER_SCOPE_SEMESTER:
            if not self.semester_id or self.assessment_id:
                raise ValidationError(_("A semester assignment requires a semester and no specific exam."))
            if self.semester.diploma_id != self.cohort.diploma_id:
                raise ValidationError(_("Semester must belong to the assignment cohort's Diploma."))
        elif self.scope == REVIEWER_SCOPE_EXAM:
            if not self.semester_id or not self.assessment_id:
                raise ValidationError(_("A Semester Exam assignment requires a semester exam."))
            if self.semester.diploma_id != self.cohort.diploma_id:
                raise ValidationError(_("Semester must belong to the assignment cohort's Diploma."))
            if (
                self.assessment.assessment_type != ASSESSMENT_TYPE_SEMESTER_EXAM
                or self.assessment.legacy_status != LEGACY_STATUS_ACTIVE
                or self.assessment.cohort_id != self.cohort_id
                or self.assessment.semester_id != self.semester_id
            ):
                raise ValidationError(_("Semester Exam must belong to the assignment cohort and semester."))
        else:
            raise ValidationError(_("Select a valid Reviewer assignment scope."))
        if self.access_mode not in {
            choice[0] for choice in REVIEWER_ACCESS_CHOICES
        }:
            raise ValidationError(_("Select a valid Reviewer access mode."))
        if self.active and self.section_id:
            raise ValidationError(
                _("Active reviewer assignments cannot be restricted to one exam section.")
            )
        if self.section_id:
            if self.section.assessment.assessment_type != ASSESSMENT_TYPE_SEMESTER_EXAM:
                raise ValidationError(_("Reviewer assignments must target a Semester Exam section."))
            if self.assessment_id and self.section.assessment_id != self.assessment_id:
                raise ValidationError(_("Legacy section restriction must belong to the assigned Semester Exam."))
            if self.section.assessment.cohort_id != self.cohort_id or (
                self.semester_id and self.section.assessment.semester_id != self.semester_id
            ):
                raise ValidationError(_("Section must belong to the assignment cohort and semester."))

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        parts = [str(self.reviewer), str(self.cohort)]
        if self.scope == REVIEWER_SCOPE_EXAM and self.assessment_id:
            parts.append(str(self.assessment))
        elif self.scope == REVIEWER_SCOPE_SEMESTER and self.semester_id:
            parts.append(str(self.semester))
        else:
            parts.append(_("All semesters"))
        return " / ".join(parts)


class ReviewerExamClaim(TimeStampedModel):
    """Exclusive anonymous claim for one whole Reviewer/Exam attempt."""

    assignment = models.ForeignKey(
        ReviewerAssignment, on_delete=models.PROTECT,
        related_name="exam_claims", verbose_name=_("reviewer assignment"),
    )
    attempt = models.ForeignKey(
        AssessmentAttempt, on_delete=models.PROTECT,
        related_name="reviewer_exam_claims", verbose_name=_("assessment attempt"),
    )
    section = models.ForeignKey(
        AssessmentLectureSection, on_delete=models.PROTECT,
        related_name="reviewer_exam_claims", verbose_name=_("section"),
        null=True, blank=True,
    )
    claimed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="claimed_reviewer_exam_work", verbose_name=_("claimed by"),
    )
    claimed_at = models.DateTimeField(_("claimed at"), auto_now_add=True)
    status = models.CharField(
        _("status"), max_length=20, choices=REVIEWER_CLAIM_STATUS_CHOICES,
        default=REVIEWER_CLAIM_IN_PROGRESS, db_index=True,
    )
    finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="finalized_reviewer_exam_claims", verbose_name=_("finalized by"),
    )
    finalized_at = models.DateTimeField(_("finalized at"), null=True, blank=True)

    class Meta:
        verbose_name = _("reviewer exam claim")
        verbose_name_plural = _("reviewer exam claims")
        constraints = [
            models.UniqueConstraint(
                fields=["attempt"],
                condition=models.Q(status=REVIEWER_CLAIM_IN_PROGRESS),
                name="uq_active_reviewer_claim_attempt",
            ),
            models.UniqueConstraint(
                fields=["assignment"],
                condition=models.Q(status=REVIEWER_CLAIM_IN_PROGRESS),
                name="uq_active_reviewer_claim_assignment",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "section"]),
            models.Index(fields=["attempt", "section"]),
        ]
