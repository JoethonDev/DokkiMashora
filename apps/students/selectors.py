"""Student dashboard aggregation selector.

Single entry point for loading all student-facing profile data.
Each method returns structured data — never rendered HTML.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.academics.constants import (
    ENROLLMENT_STATUS_ACTIVE,
    ENROLLMENT_STATUS_EXPIRED,
    ENROLLMENT_STATUS_RESTRICTED,
)
from apps.academics.models import (
    Cohort,
    Course,
    CourseEnrollment,
    CourseVersion,
    DiplomaEnrollment,
    DiplomaLectureWatchProgress,
    Lecture,
    LectureWatchProgress,
)
from apps.applications.models import ProgramApplication
from apps.applications.constants import (
    DOCUMENT_REQUEST_STATUS_REQUESTED,
    PROGRAM_APP_STATUS_PAYMENT_PENDING,
    PROGRAM_APP_STATUS_REJECTED,
)
from apps.assessments.models import AssessmentAttempt, HomeworkSubmission
from apps.assessments.services import get_student_assessments, get_student_attempts
from apps.certificates.models import GeneratedCertificate
from apps.communications.email_message import EmailMessage
from apps.entrance_exams.models import EntranceAttempt
from apps.intake_forms.models import IntakeFormRequest
from apps.payments.selectors import get_enrollment_payment_status, get_student_payment_summary
from apps.progression.constants import CERTIFICATE_STATUS_AVAILABLE
from apps.progression.models import (
    FinalExamEligibility,
    PromotionDecision,
    StudentDiplomaCompletion,
    StudentGradeItem,
    StudentSemesterProgress,
)
from apps.students.services import check_apply_eligibility
from apps.attendance.models import AttendanceRecord
from apps.academics.services import get_visible_diploma_lectures

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.students.models import StudentProfile


ALL_SECTIONS = [
    "identity", "applications", "intake_requests", "diploma_enrollments", "course_enrollments",
    "active_courses", "expired_courses", "exam_assignments",
    "assessment_attempts", "homework_submissions", "semester_progress",
    "promotion_decisions", "completions", "certificates", "eligibilities", "apply",
    "payments", "notifications", "grade_items",
    "attendance", "open_assessments",
]

_SECTION_LOADERS: dict[str, tuple] = {
    "identity": ("identity", lambda p, u: _identity_summary(p, u)),
    "applications": ("applications", lambda p, u: _get_applications(p)),
    "intake_requests": ("intake_requests", lambda p, u: _get_intake_requests(p)),
    "diploma_enrollments": ("diploma_enrollments", lambda p, u: _get_diploma_enrollments(p)),
    "course_enrollments": ("course_enrollments", lambda p, u: _get_course_enrollments(p)),
    "active_courses": ("active_courses", lambda p, u: _get_active_courses(p)),
    "expired_courses": ("expired_courses", lambda p, u: _get_expired_courses(p)),
    "exam_assignments": ("exam_assignments", lambda p, u: _get_exam_assignments(p)),
    "assessment_attempts": ("assessment_attempts", lambda p, u: _get_assessment_attempts(u)),
    "homework_submissions": ("homework_submissions", lambda p, u: _get_homework_submissions(u)),
    "semester_progress": ("semester_progress", lambda p, u: _get_semester_progress(p)),
    "promotion_decisions": ("promotion_decisions", lambda p, u: _get_promotion_decisions(p)),
    "completions": ("completions", lambda p, u: _get_completions(p)),
    "certificates": ("certificates", lambda p, u: _get_certificates(p)),
    "eligibilities": ("eligibilities", lambda p, u: _get_eligibilities(p)),
    "grade_items": ("grade_items", lambda p, u: _get_grade_items(p)),
    "payments": ("payments", lambda p, u: _get_payments(p)),
    "notifications": ("notifications", lambda p, u: _get_notifications(p)),
    "apply": ("apply", lambda p, u: _apply_eligibility(p)),
    "attendance": ("attendance", lambda p, u: _get_attendance(p)),
    "open_assessments": ("open_assessments", lambda p, u: _get_open_assessments(u)),
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

    result.setdefault("apply", None)
    result.update(_home_context(result))
    return result


def _home_context(data: dict) -> dict:
    """Build the student's actionable home from already-loaded sections."""
    from types import SimpleNamespace

    now = timezone.now()
    actions = []
    applications = data.get("applications", [])
    open_applications = [a for a in applications if a.status != PROGRAM_APP_STATUS_REJECTED]

    for application in open_applications:
        target = application.cohort.diploma.title if application.cohort_id and application.cohort.diploma_id else getattr(application.course, "title", _("Program"))
        for request in application.document_requests.all():
            if request.status == DOCUMENT_REQUEST_STATUS_REQUESTED and request.expires_at > now:
                actions.append({
                    "kind": "document", "priority": "urgent" if (request.expires_at - now).days < 7 else "normal",
                    "title": _("Upload requested documents"),
                    "description": target, "url": reverse("students:applications"),
                    "cta_label": _("Open application"), "due_at": request.expires_at, "blocked_reason": None,
                })
        for request in application.intake_requests.all():
            if request.status == "requested" and request.expires_at > now:
                actions.append({
                    "kind": "intake", "priority": "urgent" if (request.expires_at - now).days < 7 else "normal",
                    "title": _("Complete the intake form"), "description": target,
                    "url": reverse("students:applications"), "cta_label": _("Open application"),
                    "due_at": request.expires_at, "blocked_reason": None,
                })
        if application.status == PROGRAM_APP_STATUS_PAYMENT_PENDING:
            actions.append({
                "kind": "payment", "priority": "urgent", "title": _("Complete application payment"),
                "description": target, "url": reverse("students:applications"),
                "cta_label": _("Open application"), "due_at": None, "blocked_reason": None,
            })

    for attempt in data.get("exam_assignments", []):
        if attempt.status in {"assigned", "in_progress"}:
            actions.append({
                "kind": "entrance_exam", "priority": "urgent", "title": attempt.exam.title,
                "description": _("Your entrance exam is ready."),
                "url": reverse("entrance_exams:exam_access_token", args=[attempt.exam.access_token]),
                "cta_label": _("Continue exam"), "due_at": None, "blocked_reason": None,
            })

    for attempt in data.get("assessment_attempts", []):
        if attempt.status in {"assigned", "in_progress"}:
            close_at = getattr(attempt.assessment, "close_at", None)
            actions.append({
                "kind": "assessment", "priority": "normal", "title": attempt.assessment.title,
                "description": _("Assessment ready to start or continue."),
                "url": reverse("assessments:instructions", args=[attempt.assessment_id]),
                "cta_label": _("Continue assessment"), "due_at": close_at if close_at and close_at > now else None, "blocked_reason": None,
            })

    for certificate in data.get("certificates", []):
        actions.append({
            "kind": "certificate", "priority": "normal", "title": _("Certificate available"),
            "description": certificate.diploma_enrollment.cohort.diploma.title if certificate.diploma_enrollment_id else _("Your completed program"),
            "url": reverse("certificates:student_certificate_download", args=[certificate.id]),
            "cta_label": _("Download certificate"), "due_at": None, "blocked_reason": None,
        })

    for item in data.get("active_courses", []):
        actions.append({
            "kind": "lecture", "priority": "normal", "title": item["course"].title,
            "description": _("Continue your learning."),
            "url": reverse("students:learning"), "cta_label": _("Open Learning"),
            "due_at": None, "blocked_reason": None,
        })

    actions.sort(key=lambda item: (item["priority"] != "urgent", item["due_at"] is None, item["due_at"] or now))
    current_application = None
    if open_applications:
        application = open_applications[0]
        current_application = {
            "application": application,
            "status": application.status,
            "status_label": application.get_status_display(),
            "title": application.cohort.diploma.title if application.cohort_id and application.cohort.diploma_id else getattr(application.course, "title", _("Program")),
        }

    # Build lookups for open assessments
    open_assessments = data.get("open_assessments", [])
    open_by_cohort = {a.cohort_id: a for a in open_assessments if a.cohort_id}
    open_by_cv = {a.course_version_id: a for a in open_assessments if a.course_version_id}
    attempted_ids = {att.assessment_id for att in data.get("assessment_attempts", [])}

    diploma_cards = []
    for enrollment in data.get("diploma_enrollments", []):
        lectures = list(get_visible_diploma_lectures(data["student"], enrollment))
        completed_ids = set(DiplomaLectureWatchProgress.objects.filter(
            student=data["student"], diploma_enrollment=enrollment,
            lecture_id__in=[lecture.id for lecture in lectures], status="completed",
        ).values_list("lecture_id", flat=True))
        next_lecture = next((lecture for lecture in lectures if lecture.id not in completed_ids), None)
        diploma_attempt = next((attempt for attempt in data.get("assessment_attempts", []) if attempt.status in {"assigned", "in_progress"} and attempt.assessment.cohort_id == enrollment.cohort_id), None)
        if not diploma_attempt and enrollment.cohort_id in open_by_cohort:
            a = open_by_cohort[enrollment.cohort_id]
            if a.id not in attempted_ids:
                diploma_attempt = SimpleNamespace(assessment=a)
        diploma_cards.append({
            "type": "diploma", "title": enrollment.cohort.diploma.title,
            "status": enrollment.status, "enrollment_id": enrollment.id,
            "expires_at": enrollment.expires_at,
            "lecture_count": len(lectures), "completed_count": len(completed_ids),
            "next_lecture": next_lecture,
            "next_lecture_url": reverse("academics:student_diploma_lecture_view", args=[enrollment.id, next_lecture.semester_id, next_lecture.id]) if next_lecture else None,
            "next_assessment": diploma_attempt,
            "next_assessment_url": reverse("assessments:instructions", args=[diploma_attempt.assessment.id]) if diploma_attempt else None,
        })

    # Fill in open assessments for active courses that have none yet
    for card in data.get("active_courses", []):
        cv = card.get("course_version")
        if cv and not card.get("next_assessment") and cv.pk in open_by_cv:
            a = open_by_cv[cv.pk]
            if a.id not in attempted_ids:
                card["next_assessment"] = SimpleNamespace(assessment=a)
                card["next_assessment_url"] = reverse("assessments:instructions", args=[a.id])

    return {
        "next_actions": actions,
        "learning_cards": diploma_cards + data.get("active_courses", []) + data.get("expired_courses", []),
        "current_application": current_application,
        "summary": {
            "active_learning_count": len(data.get("active_courses", [])),
            "actionable_assessment_count": sum(a.status in {"assigned", "in_progress"} for a in data.get("assessment_attempts", [])),
            "open_entrance_exam_count": sum(a.status in {"assigned", "in_progress"} for a in data.get("exam_assignments", [])),
            "available_certificate_count": len(data.get("certificates", [])),
        },
    }


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def _identity_summary(profile: StudentProfile, user: User) -> dict:
    return {
        "full_name": profile.full_name,
        "email": user.email,
        "phone": profile.phone,
        "country_code": profile.country_code,
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
        .select_related("reviewed_by", "cohort__diploma", "course")
        .prefetch_related("document_requests", "intake_requests")
        .order_by("-created_at")
    )


# ---------------------------------------------------------------------------
# Intake Requests
# ---------------------------------------------------------------------------


def _get_intake_requests(student_profile: StudentProfile) -> list[IntakeFormRequest]:
    return list(
        IntakeFormRequest.objects.filter(program_application__student=student_profile)
        .select_related("program_application", "form")
        .order_by("-requested_at")
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
        .select_related("course_version__course")
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
            completed_count=Count(
                "lectures__watch_progress",
                filter=Q(
                    lectures__watch_progress__student=student_profile,
                    lectures__watch_progress__status="completed",
                ),
            ),
        )
        .filter(
            enrollments__student=student_profile,
            enrollments__status=ENROLLMENT_STATUS_ACTIVE,
            is_active=True,
        )
        .filter(
            Q(enrollments__expires_at__isnull=True) | Q(enrollments__expires_at__gt=now),
        )
        .select_related("course")
        .distinct()
        .order_by("course__title")
    )
    versions = list(qs)
    enrollments_map = {
        e.course_version_id: e
        for e in CourseEnrollment.objects.filter(
            student=student_profile,
            course_version_id__in=[cv.pk for cv in versions],
        )
    }
    lectures = list(Lecture.objects.filter(
        course_version_id__in=[cv.pk for cv in versions], is_published=True,
    ).filter(Q(release_at__isnull=True) | Q(release_at__lte=now)).order_by("course_version_id", "order"))
    completed_ids = set(LectureWatchProgress.objects.filter(
        student=student_profile, lecture_id__in=[lecture.pk for lecture in lectures], status="completed",
    ).values_list("lecture_id", flat=True))
    next_lectures = {}
    for lecture in lectures:
        if lecture.pk not in completed_ids and lecture.course_version_id not in next_lectures:
            next_lectures[lecture.course_version_id] = lecture
    next_assessments = {
        attempt.assessment.course_version_id: attempt
        for attempt in AssessmentAttempt.objects.filter(
            student=student_profile.user,
            assessment__course_version_id__in=[cv.pk for cv in versions],
            status__in={"assigned", "in_progress"},
        ).select_related("assessment").order_by("assessment__close_at")
    }
    return [
        {
            "course_version": cv,
            "course": cv.course,
            "lecture_count": cv.lecture_count,
            "completed_count": cv.completed_count,
            "expires_at": enrollments_map[cv.pk].expires_at if cv.pk in enrollments_map else None,
            "status": ENROLLMENT_STATUS_ACTIVE,
            "next_lecture": next_lectures.get(cv.pk),
            "next_lecture_url": reverse("academics:student_lecture_view", args=[cv.pk, next_lectures[cv.pk].pk]) if cv.pk in next_lectures else None,
            "next_assessment": next_assessments.get(cv.pk),
            "next_assessment_url": reverse("assessments:instructions", args=[next_assessments[cv.pk].assessment_id]) if cv.pk in next_assessments else None,
        }
        for cv in versions
    ]


# ---------------------------------------------------------------------------
# Expired / restricted courses
# ---------------------------------------------------------------------------


def _get_expired_courses(student_profile: StudentProfile) -> list[dict]:
    qs = (
        CourseEnrollment.objects.filter(student=student_profile)
        .filter(Q(status=ENROLLMENT_STATUS_EXPIRED) | Q(status=ENROLLMENT_STATUS_RESTRICTED))
        .select_related("course_version__course")
        .order_by("-created_at")
    )
    return [
        {
            "enrollment": e,
            "course_version": e.course_version,
            "course": e.course_version.course,
            "status": e.status,
        }
        for e in qs
    ]


# ---------------------------------------------------------------------------
# Entrance exam assignments
# ---------------------------------------------------------------------------


def _get_exam_assignments(profile) -> list[EntranceAttempt]:
    return list(
        EntranceAttempt.objects.filter(student=profile)
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


def _get_attendance(student_profile: StudentProfile) -> dict:
    attendance_qs = AttendanceRecord.objects.filter(student=student_profile)
    records = list(
        attendance_qs
        .select_related("session")
        .order_by("-session__date", "-id")[:50]
    )
    totals = attendance_qs.aggregate(
        total=Count("id"),
        present=Count("id", filter=Q(status="present")),
        late=Count("id", filter=Q(status="late")),
        excused=Count("id", filter=Q(status="excused")),
        absent=Count("id", filter=Q(status="absent")),
    )
    counts = {status: totals[status] or 0 for status in ("present", "late", "excused", "absent")}
    total = totals["total"] or 0
    attended = counts["present"] + counts["late"]
    return {
        "records": records,
        "counts": counts,
        "total": total,
        "attendance_percent": round(attended / total * 100, 2) if total else 0,
    }


# ---------------------------------------------------------------------------
# Open assessments
# ---------------------------------------------------------------------------


def _get_open_assessments(user):
    return get_student_assessments(user)


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
# Grade items
# ---------------------------------------------------------------------------


def _get_grade_items(student_profile: StudentProfile) -> list[StudentGradeItem]:
    return list(
        StudentGradeItem.objects.filter(
            student=student_profile,
            visible_to_student=True,
        ).select_related("component__semester", "component__cohort", "diploma_enrollment").order_by("component__semester__order")
    )


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


def _get_payments(student_profile: StudentProfile) -> dict:
    try:
        summary = get_student_payment_summary(student_profile)
        enrollments = get_enrollment_payment_status(student_profile)
        summary.update(enrollments)
        return summary
    except Exception:
        return {"total_paid": 0, "record_count": 0, "records": [], "diploma_enrollments": [], "course_enrollments": []}


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


def _apply_eligibility(student_profile: StudentProfile) -> dict:
    if any(
        check_apply_eligibility(student_profile, cohort=cohort)["allowed"]
        for cohort in Cohort.objects.filter(is_active=True).select_related("diploma").order_by("-start_date")
    ):
        return {"allowed": True, "reason_code": None, "reason": None}
    if any(
        check_apply_eligibility(student_profile, course=course)["allowed"]
        for course in Course.objects.filter(is_active=True).order_by("title")
    ):
        return {"allowed": True, "reason_code": None, "reason": None}
    return {"allowed": False, "reason_code": None, "reason": None}
