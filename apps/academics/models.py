"""Academic structure models for the LMS."""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.students.models import StudentProfile

from .constants import (
    ENROLLMENT_STATUS_CHOICES,
    ENROLLMENT_STATUS_PENDING,
)


class Diploma(TimeStampedModel):
    """A program of study composed of ordered semesters and cohorts."""

    title = models.CharField(_("title"), max_length=255)
    description = models.TextField(_("description"), blank=True)
    total_semesters = models.PositiveIntegerField(_("total semesters"), default=2)
    is_active = models.BooleanField(_("active"), default=True)
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
        unique_together = (("semester", "course"),)
        ordering = ("semester", "order")

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
    label = models.CharField(_("label"), max_length=120, default="v1")
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
        unique_together = (("course", "cohort", "label"),)
        ordering = ("course", "cohort", "label")

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
    audio_url = models.URLField(_("audio URL"), blank=True)
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
