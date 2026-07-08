from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.academics.models import Cohort, CourseVersion, DiplomaEnrollment, DiplomaLecture, Semester
from apps.core.models import TimeStampedModel

from .constants import (
    ASSESSMENT_TYPE_CHOICES,
    ASSESSMENT_TYPE_SEMESTER_EXAM,
    ATTEMPT_STATUS_ASSIGNED,
    ATTEMPT_STATUS_CHOICES,
    DELIVERY_MODE_CHOICES,
    DELIVERY_MODE_ONLINE,
    EXAM_OVERRIDE_ACTIVE,
    EXAM_OVERRIDE_STATUS_CHOICES,
    GRADE_VISIBILITY_CHOICES,
    GRADE_VISIBILITY_HIDDEN,
    HOMEWORK_STATUS_CHOICES,
    HOMEWORK_STATUS_UPLOADED,
    QUESTION_TYPE_CHOICES,
    QUESTION_TYPE_MCQ,
    QUESTION_TYPE_WRITTEN,
    SNAPSHOT_QUESTION_SOURCE_CHOICES,
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
    is_exceptional = models.BooleanField(_("exceptional"), default=False)

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
        if self.max_grade <= 0:
            raise ValidationError(_("Max grade must be positive."))


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
    points = models.DecimalField(_("points"), max_digits=5, decimal_places=2)
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
    score = models.DecimalField(_("score"), max_digits=5, decimal_places=2, null=True, blank=True)
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


class SemesterExamAccessOverride(TimeStampedModel):
    assessment = models.ForeignKey(
        Assessment, on_delete=models.CASCADE,
        related_name="access_overrides", verbose_name=_("assessment"),
    )
    diploma_enrollment = models.ForeignKey(
        "academics.DiplomaEnrollment", on_delete=models.CASCADE,
        related_name="semester_exam_access_overrides", verbose_name=_("diploma enrollment"),
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
                condition=models.Q(status=EXAM_OVERRIDE_ACTIVE),
                name="uq_active_override_per_assessment_enrollment",
            ),
        ]
        indexes = [
            models.Index(fields=["assessment", "status"]),
            models.Index(fields=["diploma_enrollment", "status"]),
            models.Index(fields=["open_at", "close_at"]),
        ]

    def clean(self):
        if self.assessment_id:
            if self.assessment.assessment_type != ASSESSMENT_TYPE_SEMESTER_EXAM:
                raise ValidationError(_("Overrides are only allowed for semester exams."))
            if self.assessment.course_version_id is not None or self.assessment.cohort_id is None or self.assessment.semester_id is None:
                raise ValidationError(_("Overrides are only allowed for diploma-scoped semester exams."))
        if self.diploma_enrollment_id and self.assessment_id:
            if self.diploma_enrollment.cohort_id != self.assessment.cohort_id:
                raise ValidationError(_("Enrollment cohort must match the assessment cohort."))
        if self.open_at and self.close_at and self.open_at >= self.close_at:
            raise ValidationError(_("open_at must be before close_at."))
        if self.max_attempts is not None and self.max_attempts < 1:
            raise ValidationError(_("max_attempts must be at least 1."))
        if not self.reason:
            raise ValidationError(_("Reason is required."))
        if self.exam_code_id and self.assessment_id:
            if self.exam_code.assessment_id != self.assessment_id or self.exam_code.diploma_enrollment_id != self.diploma_enrollment_id:
                raise ValidationError(_("Exam code must belong to the same assessment and enrollment."))

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Override {self.assessment_id} - {self.diploma_enrollment_id} ({self.status})"


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
        if self.section_id and self.score is not None and self.score > self.section.max_grade:
            raise ValidationError(_("Score cannot exceed section max grade of %(max)s.") % {"max": self.section.max_grade})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.exam_code.code_number} - {self.section}"


class ReviewerAssignment(TimeStampedModel):
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviewer_assignments", verbose_name=_("reviewer"))
    cohort = models.ForeignKey("academics.Cohort", on_delete=models.CASCADE, related_name="reviewer_assignments", verbose_name=_("cohort"))
    semester = models.ForeignKey("academics.Semester", on_delete=models.CASCADE, related_name="reviewer_assignments", verbose_name=_("semester"))
    section = models.ForeignKey("AssessmentLectureSection", on_delete=models.CASCADE, null=True, blank=True, related_name="reviewer_assignments", verbose_name=_("section"))
    active = models.BooleanField(_("active"), default=True)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_reviewer_assignments", verbose_name=_("assigned by"))

    class Meta:
        verbose_name = _("reviewer assignment")
        verbose_name_plural = _("reviewer assignments")
        constraints = [
            models.UniqueConstraint(fields=["reviewer", "cohort", "semester", "section"], name="uq_reviewer_assignment"),
        ]

    def __str__(self):
        parts = [str(self.reviewer), str(self.cohort), str(self.semester)]
        if self.section_id:
            parts.append(str(self.section))
        return " / ".join(parts)
