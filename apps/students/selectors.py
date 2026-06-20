"""Student dashboard aggregation selector.

Single entry point for loading all student-facing profile data.
Each method returns structured data — never rendered HTML.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Count, Q
from django.utils import timezone

from apps.academics.constants import (
    ENROLLMENT_STATUS_ACTIVE,
    ENROLLMENT_STATUS_EXPIRED,
    ENROLLMENT_STATUS_RESTRICTED,
)
from apps.academics.models import CourseEnrollment, CourseVersion, DiplomaEnrollment
from apps.applications.models import ProgramApplication
from apps.assessments.models import AssessmentAttempt, HomeworkSubmission
from apps.assessments.services import get_student_attempts
from apps.certificates.models import GeneratedCertificate
from apps.communications.email_message import EmailMessage
from apps.exams.constants import EXAM_TYPE_ENTRANCE
from apps.exams.models import Assignment
from apps.progression.constants import CERTIFICATE_STATUS_AVAILABLE
from apps.progression.models import (
    FinalExamEligibility,
    PromotionDecision,
    StudentDiplomaCompletion,
    StudentSemesterProgress,
)
from apps.students.services import check_reapply_eligibility

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.students.models import StudentProfile


ALL_SECTIONS = [
    "identity", "applications", "diploma_enrollments", "course_enrollments",
    "active_courses", "expired_courses", "exam_assignments",
    "assessment_attempts", "homework_submissions", "semester_progress",
    "promotion_decisions", "completions", "certificates", "eligibilities",
    "payments", "notifications",
]

_SECTION_LOADERS: dict[str, tuple] = {
    "identity": ("identity", lambda p, u: _identity_summary(p, u)),
    "applications": ("applications", lambda p, u: _get_applications(p)),
    "diploma_enrollments": ("diploma_enrollments", lambda p, u: _get_diploma_enrollments(p)),
    "course_enrollments": ("course_enrollments", lambda p, u: _get_course_enrollments(p)),
    "active_courses": ("active_courses", lambda p, u: _get_active_courses(p)),
    "expired_courses": ("expired_courses", lambda p, u: _get_expired_courses(p)),
    "exam_assignments": ("exam_assignments", lambda p, u: _get_exam_assignments(u)),
    "assessment_attempts": ("assessment_attempts", lambda p, u: _get_assessment_attempts(u)),
    "homework_submissions": ("homework_submissions", lambda p, u: _get_homework_submissions(u)),
    "semester_progress": ("semester_progress", lambda p, u: _get_semester_progress(p)),
    "promotion_decisions": ("promotion_decisions", lambda p, u: _get_promotion_decisions(p)),
    "completions": ("completions", lambda p, u: _get_completions(p)),
    "certificates": ("certificates", lambda p, u: _get_certificates(p)),
    "eligibilities": ("eligibilities", lambda p, u: _get_eligibilities(p)),
    "payments": ("payments", lambda p, u: _get_payments(p)),
    "notifications": ("notifications", lambda p, u: _get_notifications(p)),
    "reapply": ("reapply", lambda p, u: _reapply_eligibility(p)),
}


def get_dashboard_data(
    student_profile: StudentProfile,
    sections: list[str] | None = None,
) -> dict:
    """Return structured context dict for the student profile dashboard.

    Every section returns predictable keys.  Empty sections return empty lists
    or explicit None placeholders — never raises for missing optional data.

    Pass *sections* to load only a subset (e.g. ``["applications"]``).
    Defaults to ALL_SECTIONS (full dashboard payload).
    """
    if sections is None:
        sections = ALL_SECTIONS

    user = student_profile.user
    result: dict = {"student": student_profile, "user": user}

    for section_name in sections:
        loader = _SECTION_LOADERS.get(section_name)
        if loader is not None:
            key, loader_fn = loader
            result[key] = loader_fn(student_profile, user)

    result.setdefault("reapply", None)
    return result


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def _identity_summary(profile: StudentProfile, user: User) -> dict:
    return {
        "full_name": profile.full_name,
        "email": user.email,
        "phone": profile.phone,
        "national_id": profile.national_id,
        "date_of_birth": profile.date_of_birth,
        "age": profile.age,
        "gender": profile.gender,
        "address": profile.address,
        "education": profile.education,
        "specialization": profile.specialization,
        "employer": profile.employer,
        "current_job": profile.current_job,
        "church": profile.church,
        "priest_name": profile.priest_name,
        "priest_contact": profile.priest_contact,
    }


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------


def _get_applications(student_profile: StudentProfile) -> list[ProgramApplication]:
    return list(
        ProgramApplication.objects.filter(student=student_profile)
        .select_related("reviewed_by", "cohort__diploma", "course", "root_application")
        .order_by("-created_at")
    )


# ---------------------------------------------------------------------------
# Diploma enrollments
# ---------------------------------------------------------------------------


def _get_diploma_enrollments(student_profile: StudentProfile) -> list[DiplomaEnrollment]:
    return list(
        DiplomaEnrollment.objects.filter(student=student_profile)
        .select_related("cohort__diploma")
        .order_by("-created_at")
    )


# ---------------------------------------------------------------------------
# Course enrollments (all statuses)
# ---------------------------------------------------------------------------


def _get_course_enrollments(student_profile: StudentProfile) -> list[CourseEnrollment]:
    return list(
        CourseEnrollment.objects.filter(student=student_profile)
        .select_related("course_version__course", "course_version__cohort", "course_version__semester")
        .order_by("-created_at")
    )


# ---------------------------------------------------------------------------
# Active courses (currently accessible)
# ---------------------------------------------------------------------------


def _get_active_courses(student_profile: StudentProfile) -> list[dict]:
    now = timezone.now()
    qs = (
        CourseVersion.objects.annotate(
            lecture_count=Count("lectures", filter=Q(lectures__is_published=True)),
        )
        .filter(
            enrollments__student=student_profile,
            enrollments__status=ENROLLMENT_STATUS_ACTIVE,
            is_active=True,
        )
        .filter(
            Q(enrollments__expires_at__isnull=True) | Q(enrollments__expires_at__gt=now),
        )
        .select_related("course", "cohort", "semester")
        .distinct()
        .order_by("semester__order", "course__title")
    )
    enrollments_map = {
        e.course_version_id: e
        for e in CourseEnrollment.objects.filter(
            student=student_profile,
            course_version__in=qs,
        )
    }
    return [
        {
            "course_version": cv,
            "course": cv.course,
            "cohort": cv.cohort,
            "semester": cv.semester,
            "lecture_count": cv.lecture_count,
            "expires_at": enrollments_map[cv.pk].expires_at if cv.pk in enrollments_map else None,
        }
        for cv in qs
    ]


# ---------------------------------------------------------------------------
# Expired / restricted courses
# ---------------------------------------------------------------------------


def _get_expired_courses(student_profile: StudentProfile) -> list[dict]:
    qs = (
        CourseEnrollment.objects.filter(student=student_profile)
        .filter(Q(status=ENROLLMENT_STATUS_EXPIRED) | Q(status=ENROLLMENT_STATUS_RESTRICTED))
        .select_related("course_version__course", "course_version__cohort", "course_version__semester")
        .order_by("-created_at")
    )
    return [
        {
            "enrollment": e,
            "course_version": e.course_version,
            "course": e.course_version.course,
            "cohort": e.course_version.cohort,
            "semester": e.course_version.semester,
            "status": e.status,
        }
        for e in qs
    ]


# ---------------------------------------------------------------------------
# Entrance exam assignments
# ---------------------------------------------------------------------------


def _get_exam_assignments(user: User) -> list[Assignment]:
    return list(
        Assignment.objects.filter(student=user, exam__exam_type=EXAM_TYPE_ENTRANCE)
        .select_related("exam", "graded_by")
        .order_by("-assigned_at")
    )


# ---------------------------------------------------------------------------
# Normal assessment attempts
# ---------------------------------------------------------------------------


def _get_assessment_attempts(user: User) -> list[AssessmentAttempt]:
    return list(
        get_student_attempts(user).select_related(
            "assessment__course_version__course",
            "assessment__semester",
            "graded_by",
        )[:50]
    )


# ---------------------------------------------------------------------------
# Homework submissions
# ---------------------------------------------------------------------------


def _get_homework_submissions(user: User) -> list[HomeworkSubmission]:
    return list(
        HomeworkSubmission.objects.filter(student=user)
        .select_related("assessment__course_version__course", "reviewed_by", "attempt")
        .order_by("-submitted_at")[:50]
    )


# ---------------------------------------------------------------------------
# Semester progress
# ---------------------------------------------------------------------------


def _get_semester_progress(student_profile: StudentProfile) -> list[StudentSemesterProgress]:
    return list(
        StudentSemesterProgress.objects.filter(student=student_profile)
        .select_related("semester", "diploma_enrollment__cohort__diploma")
        .order_by("semester__order")
    )


# ---------------------------------------------------------------------------
# Promotion decisions
# ---------------------------------------------------------------------------


def _get_promotion_decisions(student_profile: StudentProfile) -> list[PromotionDecision]:
    return list(
        PromotionDecision.objects.filter(student=student_profile)
        .select_related("from_semester", "to_semester", "decided_by")
        .order_by("-decided_at")[:50]
    )


# ---------------------------------------------------------------------------
# Diploma completions
# ---------------------------------------------------------------------------


def _get_completions(student_profile: StudentProfile) -> list[StudentDiplomaCompletion]:
    return list(
        StudentDiplomaCompletion.objects.filter(student=student_profile)
        .select_related("diploma_enrollment__cohort__diploma", "completed_by")
    )


# ---------------------------------------------------------------------------
# Certificates (available only for student)
# ---------------------------------------------------------------------------


def _get_certificates(student_profile: StudentProfile) -> list[GeneratedCertificate]:
    return list(
        GeneratedCertificate.objects.filter(
            student=student_profile,
            status=CERTIFICATE_STATUS_AVAILABLE,
        ).select_related("diploma_enrollment__cohort__diploma", "certificate_template")
    )


# ---------------------------------------------------------------------------
# Final exam eligibility
# ---------------------------------------------------------------------------


def _get_eligibilities(student_profile: StudentProfile) -> list[FinalExamEligibility]:
    return list(
        FinalExamEligibility.objects.filter(student=student_profile)
        .select_related("diploma_enrollment__cohort__diploma", "final_assessment")
    )


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


def _get_payments(student_profile: StudentProfile) -> dict:
    try:
        from apps.payments.selectors import get_student_payment_summary
        return get_student_payment_summary(student_profile)
    except Exception:
        return {"total_paid": 0, "record_count": 0, "records": []}


# ---------------------------------------------------------------------------
# Notifications / email history
# ---------------------------------------------------------------------------


def _get_notifications(student_profile: StudentProfile) -> list[EmailMessage]:
    user = student_profile.user
    return list(
        EmailMessage.objects.filter(to_email=user.email)
        .order_by("-sent_at")[:30]
    )


# ---------------------------------------------------------------------------
# Reapply eligibility
# ---------------------------------------------------------------------------


def _reapply_eligibility(student_profile: StudentProfile) -> dict:
    return check_reapply_eligibility(student_profile)
