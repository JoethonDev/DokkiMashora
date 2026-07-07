"""Academic structure models for the LMS."""

import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.students.models import StudentProfile

from .constants import (
    AUDIO_PROCESSING_PENDING,
    AUDIO_PROCESSING_STATUS_CHOICES,
    ENROLLMENT_STATUS_CHOICES,
    ENROLLMENT_STATUS_PENDING,
    SCOPE_CHOICES,
    SCOPE_COURSE_VERSION,
    SCOPE_DIPLOMA_COHORT,
    SCOPE_DIPLOMA_SEMESTER,
    SCOPE_SEMESTER_COHORT,
    SCOPE_STANDALONE_COURSE,
    SCOPE_WHOLE_DIPLOMA,
    VIOLATION_CHOICES,
    VIOLATION_NONE,
    WATCH_STATUS_CHOICES,
    WATCH_STATUS_IN_PROGRESS,
)


class Diploma(TimeStampedModel):
    """A program of study composed of ordered semesters and cohorts."""

    title = models.CharField(_("title"), max_length=255)
    description = models.TextField(_("description"), blank=True)
    total_semesters = models.PositiveIntegerField(_("total semesters"), default=2)
    is_active = models.BooleanField(_("active"), default=True)
    video_tracking_enabled = models.BooleanField(_("video tracking enabled"), default=False)
    video_completion_percent = models.PositiveSmallIntegerField(
        _("video completion percent"),
        default=80,
        validators=[MinValueValidator(1), MaxValueValidator(100)],
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_diplomas",
        verbose_name=_("created by"),
    )

    class Meta:
        verbose_name = _("diploma")
        verbose_name_plural = _("diplomas")
        ordering = ("title",)

    def __str__(self):
        return self.title


class Semester(TimeStampedModel):
    """An ordered term within a diploma."""

    diploma = models.ForeignKey(
        Diploma,
        on_delete=models.CASCADE,
        related_name="semesters",
        verbose_name=_("diploma"),
    )
    order = models.PositiveIntegerField(_("order"), db_index=True)
    title = models.CharField(_("title"), max_length=255)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("semester")
        verbose_name_plural = _("semesters")
        unique_together = (("diploma", "order"),)
        ordering = ("diploma", "order")

    def __str__(self):
        return f"{self.diploma.title} - {self.title}"


class Course(TimeStampedModel):
    """A course definition that can be part of a semester or standalone."""

    title = models.CharField(_("title"), max_length=255)
    description = models.TextField(_("description"), blank=True)
    is_standalone = models.BooleanField(_("standalone"), default=False)
    is_active = models.BooleanField(_("active"), default=True)
    video_tracking_enabled = models.BooleanField(_("video tracking enabled"), default=False)
    video_completion_percent = models.PositiveSmallIntegerField(
        _("video completion percent"),
        default=80,
        validators=[MinValueValidator(1), MaxValueValidator(100)],
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_courses",
        verbose_name=_("created by"),
    )

    class Meta:
        verbose_name = _("course")
        verbose_name_plural = _("courses")
        ordering = ("title",)

    @property
    def is_attached_to_diploma(self):
        return self.pk and self.semester_courses.exists()

    def clean(self):
        if self.is_standalone and self.pk:
            if self.semester_courses.exists():
                raise ValidationError(_("Standalone course cannot be linked to a semester."))

    def __str__(self):
        return self.title


class SemesterCourse(TimeStampedModel):
    """Links a course to a semester with an ordering."""

    semester = models.ForeignKey(
        Semester,
        on_delete=models.CASCADE,
        related_name="semester_courses",
        verbose_name=_("semester"),
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="semester_courses",
        verbose_name=_("course"),
    )
    order = models.PositiveIntegerField(_("order"), default=1)

    class Meta:
        verbose_name = _("semester course")
        verbose_name_plural = _("semester courses")
        ordering = ("semester", "order")
        constraints = [
            models.UniqueConstraint(
                fields=["semester", "course"],
                name="uq_semester_course",
            ),
            models.UniqueConstraint(
                fields=["course"],
                name="uq_diploma_course_owner",
            ),
        ]

    def clean(self):
        if self.course_id and self.course.is_standalone:
            raise ValidationError(_("Standalone course cannot be linked to a semester."))

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.semester} - {self.course}"


class Cohort(TimeStampedModel):
    """A group of students progressing through a diploma together."""

    diploma = models.ForeignKey(
        Diploma,
        on_delete=models.CASCADE,
        related_name="cohorts",
        verbose_name=_("diploma"),
    )
    name = models.CharField(_("name"), max_length=255)
    start_date = models.DateField(_("start date"))
    end_date = models.DateField(_("end date"), null=True, blank=True)
    is_active = models.BooleanField(_("active"), default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_cohorts",
        verbose_name=_("created by"),
    )

    class Meta:
        verbose_name = _("cohort")
        verbose_name_plural = _("cohorts")
        unique_together = (("diploma", "name"),)
        ordering = ("-start_date",)

    def __str__(self):
        return f"{self.diploma.title} - {self.name}"


class CourseVersion(TimeStampedModel):
    """A version of a course delivered to a cohort (or standalone)."""

    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="versions",
        verbose_name=_("course"),
    )
    cohort = models.ForeignKey(
        Cohort,
        on_delete=models.CASCADE,
        related_name="course_versions",
        verbose_name=_("cohort"),
        null=True,
        blank=True,
    )
    semester = models.ForeignKey(
        Semester,
        on_delete=models.CASCADE,
        related_name="course_versions",
        verbose_name=_("semester"),
        null=True,
        blank=True,
    )
    label = models.CharField(_("label"), max_length=120, default="v1", blank=True)
    access_duration_days = models.PositiveIntegerField(
        _("access duration (days)"),
        null=True,
        blank=True,
        help_text=_("Number of days the student can access this course after activation. Leave blank for unlimited."),
    )
    is_active = models.BooleanField(_("active"), default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_course_versions",
        verbose_name=_("created by"),
    )

    class Meta:
        verbose_name = _("course version")
        verbose_name_plural = _("course versions")
        ordering = ("course", "cohort", "label")
        constraints = [
            models.UniqueConstraint(
                fields=["course", "cohort"],
                condition=models.Q(cohort__isnull=False),
                name="uq_diploma_course_cohort_version",
            ),
            models.UniqueConstraint(
                fields=["course", "label"],
                condition=models.Q(cohort__isnull=True),
                name="uq_standalone_course_version_label",
            ),
        ]

    def __str__(self):
        cohort_label = self.cohort.name if self.cohort else _("standalone")
        return f"{self.course.title} - {cohort_label} - {self.label}"


class Lecture(TimeStampedModel):
    """A content unit within a course version (YouTube embed, audio, etc.)."""

    course_version = models.ForeignKey(
        CourseVersion,
        on_delete=models.CASCADE,
        related_name="lectures",
        verbose_name=_("course version"),
    )
    order = models.PositiveIntegerField(_("order"), db_index=True)
    title = models.CharField(_("title"), max_length=255)
    description = models.TextField(_("description"), blank=True)
    youtube_url = models.URLField(_("YouTube URL"), blank=True)
    youtube_video_id = models.CharField(
        _("YouTube video ID"), max_length=11, blank=True, db_index=True,
    )
    youtube_duration_seconds = models.PositiveIntegerField(
        _("YouTube duration (seconds)"), null=True, blank=True,
    )
    youtube_metadata_fetched_at = models.DateTimeField(
        _("YouTube metadata fetched at"), null=True, blank=True,
    )
    audio_url = models.URLField(_("audio URL"), blank=True)
    audio_storage_key = models.CharField(_("audio storage key"), max_length=500, blank=True)
    audio_original_filename = models.CharField(_("original filename"), max_length=500, blank=True)
    audio_content_type = models.CharField(_("content type"), max_length=100, blank=True)
    audio_byte_size = models.PositiveIntegerField(_("file size (bytes)"), null=True, blank=True)
    audio_checksum = models.CharField(_("checksum"), max_length=64, blank=True)
    audio_hls_playlist_key = models.CharField(_("HLS playlist key"), max_length=500, blank=True)
    audio_hls_segment_prefix = models.CharField(_("HLS segment prefix"), max_length=500, blank=True)
    audio_processing_status = models.CharField(
        _("processing status"),
        max_length=20,
        choices=AUDIO_PROCESSING_STATUS_CHOICES,
        default=AUDIO_PROCESSING_PENDING,
    )
    audio_processing_error = models.TextField(_("processing error"), blank=True)
    release_at = models.DateTimeField(
        _("release at"),
        null=True,
        blank=True,
        help_text=_("When the lecture becomes visible to students. Leave blank for immediate availability."),
    )
    is_published = models.BooleanField(_("published"), default=False)

    class Meta:
        verbose_name = _("lecture")
        verbose_name_plural = _("lectures")
        unique_together = (("course_version", "order"),)
        ordering = ("course_version", "order")

    def __str__(self):
        return f"{self.course_version} - {self.order}. {self.title}"

    _YT_VIDEO_ID_RE = re.compile(
        r"(?:youtube\.com/(?:watch\?.*?v=|embed/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})"
    )

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        update_fields = kwargs.get("update_fields")
        if not update_fields and self.youtube_url and not self.youtube_video_id:
            m = self._YT_VIDEO_ID_RE.search(self.youtube_url)
            video_id = m.group(1) if m else ""
            if video_id:
                self.youtube_video_id = video_id
                self.youtube_metadata_fetched_at = timezone.now()
                self.save(update_fields=["youtube_video_id", "youtube_metadata_fetched_at"])

    @property
    def is_released(self):
        """True if the lecture is visible based on its release schedule."""
        if not self.is_published:
            return False
        if self.release_at is None:
            return True
        return timezone.now() >= self.release_at


class DiplomaEnrollment(TimeStampedModel):
    """Enrollment of a student in a diploma cohort."""

    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="diploma_enrollments",
        verbose_name=_("student"),
    )
    cohort = models.ForeignKey(
        Cohort,
        on_delete=models.CASCADE,
        related_name="enrollments",
        verbose_name=_("cohort"),
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=ENROLLMENT_STATUS_CHOICES,
        default=ENROLLMENT_STATUS_PENDING,
        db_index=True,
    )
    activated_at = models.DateTimeField(_("activated at"), null=True, blank=True)
    expires_at = models.DateTimeField(_("expires at"), null=True, blank=True)

    class Meta:
        verbose_name = _("diploma enrollment")
        verbose_name_plural = _("diploma enrollments")
        unique_together = (("student", "cohort"),)
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.student} - {self.cohort}"


class CourseEnrollment(TimeStampedModel):
    """Enrollment of a student in a course version."""

    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="course_enrollments",
        verbose_name=_("student"),
    )
    course_version = models.ForeignKey(
        CourseVersion,
        on_delete=models.CASCADE,
        related_name="enrollments",
        verbose_name=_("course version"),
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=ENROLLMENT_STATUS_CHOICES,
        default=ENROLLMENT_STATUS_PENDING,
        db_index=True,
    )
    activated_at = models.DateTimeField(_("activated at"), null=True, blank=True)
    expires_at = models.DateTimeField(_("expires at"), null=True, blank=True)

    class Meta:
        verbose_name = _("course enrollment")
        verbose_name_plural = _("course enrollments")
        unique_together = (("student", "course_version"),)
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.student} - {self.course_version}"

    @property
    def is_access_expired(self):
        """True if the access window has passed."""
        if self.expires_at is None:
            return False
        return timezone.now() >= self.expires_at


class TeacherAssignment(TimeStampedModel):
    """Generalized teacher scope: one scope type per assignment row."""

    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="teacher_assignments",
        verbose_name=_("teacher"),
    )
    scope_type = models.CharField(
        _("scope type"),
        max_length=24,
        choices=SCOPE_CHOICES,
        db_index=True,
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="teacher_assignments",
        verbose_name=_("course"),
    )
    course_version = models.ForeignKey(
        CourseVersion,
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="teacher_assignments",
        verbose_name=_("course version"),
    )
    diploma = models.ForeignKey(
        Diploma,
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="teacher_assignments",
        verbose_name=_("diploma"),
    )
    semester = models.ForeignKey(
        Semester,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="teacher_assignments",
        verbose_name=_("semester"),
    )
    cohort = models.ForeignKey(
        Cohort,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="teacher_assignments_new",
        verbose_name=_("cohort"),
    )
    active = models.BooleanField(_("active"), default=True, db_index=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ta_assigned_by",
        verbose_name=_("assigned by"),
    )
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="ta_revoked_by",
        verbose_name=_("revoked by"),
    )
    revoked_at = models.DateTimeField(_("revoked at"), null=True, blank=True)
    revoke_reason = models.TextField(_("revoke reason"), blank=True)

    class Meta:
        verbose_name = _("teacher assignment")
        verbose_name_plural = _("teacher assignments")
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("teacher", "scope_type", "cohort"),
                condition=models.Q(active=True),
                name="unique_active_teacher_scope_cohort",
            ),
        ]
        indexes = [
            models.Index(fields=("teacher", "active")),
            models.Index(fields=("scope_type", "active")),
        ]

    def __str__(self):
        return f"{self.teacher} - {self.get_scope_type_display()}"

    def clean(self):
        if self.scope_type == SCOPE_STANDALONE_COURSE:
            if not self.course_id:
                raise ValidationError(_("Standalone course scope requires a course."))
            if not Course.objects.filter(id=self.course_id, is_standalone=True).exists():
                raise ValidationError(_("Course must be standalone."))
            if self.diploma_id or self.semester_id or self.cohort_id:
                raise ValidationError(_("Standalone course scope must not have diploma/semester/cohort."))
        elif self.scope_type == SCOPE_WHOLE_DIPLOMA:
            if not self.diploma_id:
                raise ValidationError(_("Whole diploma scope requires a diploma."))
            if self.course_id or self.semester_id or self.cohort_id:
                raise ValidationError(_("Whole diploma scope must not have course/semester/cohort."))
        elif self.scope_type == SCOPE_DIPLOMA_SEMESTER:
            if not self.semester_id:
                raise ValidationError(_("Diploma semester scope requires a semester."))
            if self.course_id or self.cohort_id:
                raise ValidationError(_("Diploma semester scope must not have course/cohort."))
        elif self.scope_type == SCOPE_DIPLOMA_COHORT:
            if not self.cohort_id:
                raise ValidationError(_("Diploma cohort scope requires a cohort."))
            if self.course_id or self.semester_id:
                raise ValidationError(_("Diploma cohort scope must not have course/semester."))
        elif self.scope_type == SCOPE_SEMESTER_COHORT:
            if not self.semester_id or not self.cohort_id:
                raise ValidationError(_("Semester-within-cohort scope requires both semester and cohort."))
            if self.course_id:
                raise ValidationError(_("Semester-within-cohort scope must not have a course."))
        elif self.scope_type == SCOPE_COURSE_VERSION:
            if not self.course_version_id:
                raise ValidationError(_("Course version scope requires a course version."))
            if self.course_id or self.diploma_id or self.semester_id or self.cohort_id:
                raise ValidationError(_("Course version scope must not have course/diploma/semester/cohort."))
            if TeacherAssignment.objects.filter(
                teacher=self.teacher, scope_type=SCOPE_COURSE_VERSION,
                course_version=self.course_version, active=True,
            ).exclude(pk=self.pk).exists():
                raise ValidationError(_("Teacher already has an active assignment for this course version."))
        else:
            raise ValidationError(_("Unknown scope type."))

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class ModeratorStudentAssignment(TimeStampedModel):
    """Explicit moderator scope for one student inside one cohort."""

    moderator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="moderator_student_assignments",
        verbose_name=_("moderator"),
    )
    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.PROTECT,
        related_name="moderator_assignments",
        verbose_name=_("student"),
    )
    cohort = models.ForeignKey(
        Cohort,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="moderator_assignments",
        verbose_name=_("cohort"),
    )
    active = models.BooleanField(_("active"), default=True, db_index=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="moderator_assignments_created",
        verbose_name=_("assigned by"),
    )
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="moderator_assignments_revoked",
        verbose_name=_("revoked by"),
        null=True,
        blank=True,
    )
    revoked_at = models.DateTimeField(_("revoked at"), null=True, blank=True)
    revoke_reason = models.TextField(_("revoke reason"), blank=True)

    class Meta:
        verbose_name = _("moderator student assignment")
        verbose_name_plural = _("moderator student assignments")
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("moderator", "student", "cohort"),
                condition=models.Q(active=True),
                name="unique_active_moderator_student_cohort_assignment",
            ),
        ]
        indexes = [
            models.Index(fields=("moderator", "active")),
            models.Index(fields=("student", "cohort", "active")),
        ]

    def __str__(self):
        return f"{self.moderator} - {self.student} - {self.cohort}"


class StudentCluster(TimeStampedModel):
    """Group of students within a cohort. A student belongs to at most one cluster per cohort."""

    name = models.CharField(_("name"), max_length=255)
    cohort = models.ForeignKey(
        Cohort, on_delete=models.CASCADE, related_name="clusters", verbose_name=_("cohort")
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name=_("created by")
    )

    class Meta:
        verbose_name = _("student cluster")
        verbose_name_plural = _("student clusters")
        unique_together = (("name", "cohort"),)

    def __str__(self):
        return f"{self.name} ({self.cohort.name})"


class StudentClusterMembership(TimeStampedModel):
    """Links a student to a cluster. One cluster per student per cohort."""

    cluster = models.ForeignKey(
        StudentCluster, on_delete=models.CASCADE, related_name="memberships", verbose_name=_("cluster")
    )
    student = models.ForeignKey(
        StudentProfile, on_delete=models.CASCADE, related_name="cluster_memberships", verbose_name=_("student")
    )
    cohort = models.ForeignKey(
        Cohort, on_delete=models.CASCADE, related_name="cluster_memberships", verbose_name=_("cohort"),
        null=True, blank=True,
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name=_("assigned by")
    )

    class Meta:
        verbose_name = _("student cluster membership")
        verbose_name_plural = _("student cluster memberships")
        constraints = [
            models.UniqueConstraint(
                fields=("student", "cluster"),
                name="unique_student_per_cohort_cluster",
                violation_error_message=_("A student can belong to at most one cluster per cohort."),
            ),
            models.UniqueConstraint(
                fields=("student", "cohort"),
                name="unique_student_per_cohort",
                violation_error_message=_("A student can belong to at most one cluster per cohort."),
            ),
        ]

    def clean(self):
        if self.cluster_id and self.student_id:
            if not DiplomaEnrollment.objects.filter(
                student=self.student,
                cohort=self.cluster.cohort,
            ).exists():
                raise ValidationError(_("Student is not enrolled in the cluster's cohort."))

    def save(self, *args, **kwargs):
        self.cohort = self.cluster.cohort
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.student} → {self.cluster.name}"


class ModeratorAssignment(TimeStampedModel):
    """Moderator scoped to a course version + student scope (cluster or manual)."""

    moderator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="moderator_assignments",
        verbose_name=_("moderator"),
    )
    course_version = models.ForeignKey(
        CourseVersion,
        on_delete=models.PROTECT,
        related_name="moderator_assignments",
        verbose_name=_("course version"),
    )
    cohort = models.ForeignKey(
        Cohort, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="moderator_version_assignments", verbose_name=_("cohort")
    )
    cluster = models.ForeignKey(
        StudentCluster,
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="moderator_assignments",
        verbose_name=_("cluster"),
    )
    active = models.BooleanField(_("active"), default=True, db_index=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="mod_assignments_created",
        verbose_name=_("assigned by"),
    )

    class Meta:
        verbose_name = _("moderator assignment")
        verbose_name_plural = _("moderator assignments")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("moderator", "active")),
            models.Index(fields=("course_version", "active")),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("moderator", "course_version", "cohort", "cluster"),
                condition=models.Q(active=True),
                name="unique_active_moderator_assignment",
            ),
        ]

    def __str__(self):
        return f"{self.moderator} - {self.course_version} ({self.cohort})"

    def clean(self):
        if self.cluster_id and self.cluster.cohort_id != self.cohort_id:
            raise ValidationError(_("Cluster must belong to the same cohort as the assignment."))
        if self.course_version_id and self.cohort_id and self.course_version.cohort_id != self.cohort_id:
            raise ValidationError(_("Course version must belong to the same cohort as the assignment."))

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class LectureWatchProgress(TimeStampedModel):
    """Server-validated video watch progress for a student on a lecture."""

    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="lecture_watch_progress",
        verbose_name=_("student"),
    )
    lecture = models.ForeignKey(
        Lecture,
        on_delete=models.CASCADE,
        related_name="watch_progress",
        verbose_name=_("lecture"),
    )
    diploma_enrollment = models.ForeignKey(
        DiplomaEnrollment,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="watch_progress",
        verbose_name=_("diploma enrollment"),
    )
    course_enrollment = models.ForeignKey(
        CourseEnrollment,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="watch_progress",
        verbose_name=_("course enrollment"),
    )
    validated_contiguous_seconds = models.DecimalField(
        _("validated contiguous seconds"),
        max_digits=10,
        decimal_places=3,
        default=0,
    )
    resume_position_seconds = models.DecimalField(
        _("resume position seconds"),
        max_digits=10,
        decimal_places=3,
        default=0,
    )
    active_session_id = models.UUIDField(
        _("active session ID"), null=True, blank=True,
    )
    last_sequence = models.PositiveIntegerField(_("last sequence"), default=0)
    last_position_seconds = models.DecimalField(
        _("last position seconds"),
        max_digits=10,
        decimal_places=3,
        default=0,
    )
    last_player_state = models.CharField(_("last player state"), max_length=16, blank=True)
    last_checkpoint_at = models.DateTimeField(_("last checkpoint at"), null=True, blank=True)
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=WATCH_STATUS_CHOICES,
        default=WATCH_STATUS_IN_PROGRESS,
        db_index=True,
    )
    violation_reason = models.CharField(
        _("violation reason"),
        max_length=24,
        choices=VIOLATION_CHOICES,
        default=VIOLATION_NONE,
    )
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)

    class Meta:
        verbose_name = _("lecture watch progress")
        verbose_name_plural = _("lecture watch progress")
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(diploma_enrollment__isnull=False, course_enrollment__isnull=True)
                    | models.Q(diploma_enrollment__isnull=True, course_enrollment__isnull=False)
                ),
                name="lwp_exactly_one_enrollment",
            ),
            models.UniqueConstraint(
                fields=["student", "lecture", "diploma_enrollment"],
                condition=models.Q(diploma_enrollment__isnull=False),
                name="uq_lwp_student_lecture_diploma",
            ),
            models.UniqueConstraint(
                fields=["student", "lecture", "course_enrollment"],
                condition=models.Q(course_enrollment__isnull=False),
                name="uq_lwp_student_lecture_course",
            ),
            models.CheckConstraint(
                condition=models.Q(validated_contiguous_seconds__gte=0),
                name="lwp_validated_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["student", "lecture"]),
            models.Index(fields=["active_session_id"]),
        ]

    def __str__(self):
        return f"{self.student} - {self.lecture} ({self.status})"
