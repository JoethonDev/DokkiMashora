from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.academics.models import Cohort, CourseEnrollment, CourseVersion, DiplomaEnrollment, Semester
from apps.core.models import TimeStampedModel
from apps.students.models import StudentProfile

from .constants import (
    ATTENDANCE_STATUS_CHOICES,
    ATTENDANCE_STATUS_PRESENT,
    SESSION_STATUS_CHOICES,
    SESSION_STATUS_SCHEDULED,
)


class AttendanceCode(TimeStampedModel):
    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="attendance_codes",
        verbose_name=_("student"),
    )
    diploma_enrollment = models.ForeignKey(
        DiplomaEnrollment,
        on_delete=models.CASCADE,
        related_name="attendance_codes",
        null=True,
        blank=True,
        verbose_name=_("diploma enrollment"),
    )
    course_enrollment = models.ForeignKey(
        CourseEnrollment,
        on_delete=models.CASCADE,
        related_name="attendance_codes",
        null=True,
        blank=True,
        verbose_name=_("course enrollment"),
    )
    token_hash = models.CharField(
        _("token hash"),
        max_length=64,
        unique=True,
        db_index=True,
    )
    display_code = models.CharField(
        _("display code"),
        max_length=20,
        unique=True,
        null=True,
        blank=True,
    )
    active = models.BooleanField(_("active"), default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_attendance_codes",
        verbose_name=_("created by"),
    )

    class Meta:
        verbose_name = _("attendance code")
        verbose_name_plural = _("attendance codes")
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(diploma_enrollment__isnull=False, course_enrollment__isnull=True)
                    | models.Q(diploma_enrollment__isnull=True, course_enrollment__isnull=False)
                ),
                name="ck_attendance_code_exactly_one_enrollment",
            ),
            models.UniqueConstraint(
                fields=["diploma_enrollment"],
                condition=models.Q(diploma_enrollment__isnull=False, active=True),
                name="uq_attendance_code_active_diploma_enrollment",
            ),
            models.UniqueConstraint(
                fields=["course_enrollment"],
                condition=models.Q(course_enrollment__isnull=False, active=True),
                name="uq_attendance_code_active_course_enrollment",
            ),
        ]

    def clean(self):
        if bool(self.diploma_enrollment_id) == bool(self.course_enrollment_id):
            raise ValidationError(
                _("Exactly one of diploma enrollment or course enrollment must be set.")
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.display_code or self.token_hash[:12]


class AttendanceSession(TimeStampedModel):
    cohort = models.ForeignKey(
        Cohort,
        on_delete=models.CASCADE,
        related_name="attendance_sessions",
        null=True,
        blank=True,
        verbose_name=_("cohort"),
    )
    course_version = models.ForeignKey(
        CourseVersion,
        on_delete=models.CASCADE,
        related_name="attendance_sessions",
        null=True,
        blank=True,
        verbose_name=_("course version"),
    )
    semester = models.ForeignKey(
        Semester,
        on_delete=models.CASCADE,
        related_name="attendance_sessions",
        null=True,
        blank=True,
        verbose_name=_("semester"),
    )
    date = models.DateField(_("date"))
    title = models.CharField(_("title"), max_length=255, blank=True, default="")
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=SESSION_STATUS_CHOICES,
        default=SESSION_STATUS_SCHEDULED,
        db_index=True,
    )
    open_at = models.DateTimeField(_("open at"))
    close_at = models.DateTimeField(_("close at"))
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="opened_attendance_sessions",
        verbose_name=_("opened by"),
    )
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="closed_attendance_sessions",
        verbose_name=_("closed by"),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_attendance_sessions",
        verbose_name=_("created by"),
    )

    class Meta:
        verbose_name = _("attendance session")
        verbose_name_plural = _("attendance sessions")
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(cohort__isnull=False, course_version__isnull=True)
                    | models.Q(cohort__isnull=True, course_version__isnull=False)
                ),
                name="ck_attendance_session_exactly_one_target",
            ),
        ]

    def clean(self):
        if bool(self.cohort_id) == bool(self.course_version_id):
            raise ValidationError(
                _("Exactly one of cohort or course version must be set.")
            )
        if self.open_at and self.close_at and self.open_at >= self.close_at:
            raise ValidationError(_("open_at must be before close_at."))
        if self.semester_id and not self.cohort_id:
            raise ValidationError(
                _("Semester can only be set when targeting a cohort.")
            )
        if self.semester_id and self.cohort_id:
            if self.semester.diploma_id != self.cohort.diploma_id:
                raise ValidationError(
                    _("Semester must belong to the cohort's diploma.")
                )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.title or _('Attendance')} - {self.date}"


class AttendanceRecord(TimeStampedModel):
    session = models.ForeignKey(
        AttendanceSession,
        on_delete=models.CASCADE,
        related_name="records",
        verbose_name=_("session"),
    )
    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="attendance_records",
        verbose_name=_("student"),
    )
    diploma_enrollment = models.ForeignKey(
        DiplomaEnrollment,
        on_delete=models.CASCADE,
        related_name="attendance_records",
        null=True,
        blank=True,
        verbose_name=_("diploma enrollment"),
    )
    course_enrollment = models.ForeignKey(
        CourseEnrollment,
        on_delete=models.CASCADE,
        related_name="attendance_records",
        null=True,
        blank=True,
        verbose_name=_("course enrollment"),
    )
    attendance_code = models.ForeignKey(
        AttendanceCode,
        on_delete=models.CASCADE,
        related_name="records",
        verbose_name=_("attendance code"),
    )
    scanned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="scanned_attendance_records",
        verbose_name=_("scanned by"),
    )
    scanned_at = models.DateTimeField(auto_now_add=True, verbose_name=_("scanned at"))
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=ATTENDANCE_STATUS_CHOICES,
        default=ATTENDANCE_STATUS_PRESENT,
    )
    metadata = models.JSONField(_("metadata"), default=dict, blank=True)

    class Meta:
        verbose_name = _("attendance record")
        verbose_name_plural = _("attendance records")
        constraints = [
            models.UniqueConstraint(
                fields=["session", "diploma_enrollment"],
                condition=models.Q(diploma_enrollment__isnull=False),
                name="uq_attendance_record_session_diploma",
            ),
            models.UniqueConstraint(
                fields=["session", "course_enrollment"],
                condition=models.Q(course_enrollment__isnull=False),
                name="uq_attendance_record_session_course",
            ),
        ]

    def __str__(self):
        return f"{self.student} - {self.session} - {self.status}"
