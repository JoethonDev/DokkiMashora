"""Access policy and enrollment business logic for academics."""

from datetime import datetime, timedelta

from django.db.models import Q
from django.utils import timezone

from apps.academics.constants import (
    ENROLLMENT_STATUS_ACTIVE,
    ENROLLMENT_STATUS_EXPIRED,
    ENROLLMENT_STATUS_PENDING,
)
from apps.academics.models import (
    CourseEnrollment,
    CourseVersion,
    DiplomaEnrollment,
    Lecture,
)


def activate_diploma_enrollment(enrollment: DiplomaEnrollment) -> DiplomaEnrollment:
    """Activate a pending diploma enrollment."""
    enrollment.status = ENROLLMENT_STATUS_ACTIVE
    enrollment.activated_at = timezone.now()
    if enrollment.cohort.end_date is not None:
        enrollment.expires_at = datetime.combine(
            enrollment.cohort.end_date,
            datetime.max.time(),
            tzinfo=timezone.get_current_timezone(),
        )
    enrollment.save(update_fields=["status", "activated_at", "expires_at", "updated_at"])
    return enrollment


def activate_course_enrollment(enrollment: CourseEnrollment) -> CourseEnrollment:
    """Activate a pending course enrollment and compute expiry from access duration."""
    enrollment.status = ENROLLMENT_STATUS_ACTIVE
    enrollment.activated_at = timezone.now()
    duration = enrollment.course_version.access_duration_days
    if duration is not None:
        enrollment.expires_at = enrollment.activated_at + timedelta(days=duration)
    enrollment.save(update_fields=["status", "activated_at", "expires_at", "updated_at"])
    return enrollment


def expire_stale_enrollments():
    """Mark any active enrollments whose expiry has passed as expired."""
    now = timezone.now()
    DiplomaEnrollment.objects.filter(
        status=ENROLLMENT_STATUS_ACTIVE,
        expires_at__lte=now,
    ).update(status=ENROLLMENT_STATUS_EXPIRED)
    CourseEnrollment.objects.filter(
        status=ENROLLMENT_STATUS_ACTIVE,
        expires_at__lte=now,
    ).update(status=ENROLLMENT_STATUS_EXPIRED)


def get_student_course_versions(student_profile):
    """Return course versions the student is enrolled in (active only)."""
    return CourseVersion.objects.filter(
        enrollments__student=student_profile,
        enrollments__status=ENROLLMENT_STATUS_ACTIVE,
        is_active=True,
    ).select_related("course", "cohort", "semester")


def get_visible_lectures(student_profile, course_version):
    if not can_access_course(student_profile, course_version):
        return Lecture.objects.none()
    now = timezone.now()
    return (
        course_version.lectures.filter(
            Q(release_at__isnull=True) | Q(release_at__lte=now),
            is_published=True,
        )
        .order_by("order")
    )


def can_access_course(student_profile, course_version) -> bool:
    """Check whether the student has an active, non-expired enrollment."""
    enrollment = _get_course_enrollment(student_profile, course_version)
    if enrollment is None:
        return False
    if enrollment.status != ENROLLMENT_STATUS_ACTIVE:
        return False
    if enrollment.is_access_expired:
        return False
    return True


def can_access_lecture(student_profile, lecture) -> bool:
    """Check whether the student can view a specific lecture."""
    if not can_access_course(student_profile, lecture.course_version):
        return False
    return lecture.is_released


def _get_course_enrollment(student_profile, course_version):
    return CourseEnrollment.objects.filter(
        student=student_profile,
        course_version=course_version,
    ).first()


def enroll_student_in_course(student_profile, course_version) -> CourseEnrollment:
    """Create or get a pending course enrollment for a student."""
    enrollment, _ = CourseEnrollment.objects.get_or_create(
        student=student_profile,
        course_version=course_version,
        defaults={"status": ENROLLMENT_STATUS_PENDING},
    )
    return enrollment


def enroll_student_in_diploma(student_profile, cohort) -> DiplomaEnrollment:
    """Create or get a pending diploma enrollment for a student."""
    enrollment, _ = DiplomaEnrollment.objects.get_or_create(
        student=student_profile,
        cohort=cohort,
        defaults={"status": ENROLLMENT_STATUS_PENDING},
    )
    return enrollment
