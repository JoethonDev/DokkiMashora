"""Student dashboard aggregation selector.

Single entry point for loading all student-facing profile data.
Each method returns structured data — never rendered HTML.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
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
    DiplomaLecture,
    DiplomaLectureWatchProgress,
    Lecture,
    LectureWatchProgress,
    Semester,
)
from apps.academics.services import (
    course_progress_for_enrollment,
    diploma_progress_for_enrollment,
    get_student_diploma_access,
    get_visible_semester_ids_by_enrollment,
)
from apps.applications.constants import (
    DOCUMENT_REQUEST_STATUS_REQUESTED,
    PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED,
    PROGRAM_APP_STATUS_INVITED,
    PROGRAM_APP_STATUS_PAYMENT_PENDING,
    PROGRAM_APP_STATUS_REJECTED,
)
from apps.applications.models import ProgramApplication
from apps.assessments import constants as assessment_constants
from apps.assessments.models import (
    AssessmentAttempt,
    AssessmentLectureSection,
    HomeworkSubmission,
    OfflineSectionGrade,
    SemesterExamCode,
)
from apps.assessments.services import get_student_assessments, get_student_attempts
from apps.attendance.models import AttendanceRecord
from apps.attendance.services import (
    get_attendance_evaluation_percent,
    get_attendance_summaries,
)
from apps.certificates.models import GeneratedCertificate
from apps.communications.email_message import EmailMessage
from apps.entrance_exams.constants import (
    ATTEMPT_STATUS_COMPLETED,
    ATTEMPT_STATUS_GRADED,
    ATTEMPT_STATUS_IN_PROGRESS,
)
from apps.entrance_exams.models import EntranceAttempt
from apps.entrance_exams.services import attempt_decision
from apps.intake_forms.models import IntakeFormRequest
from apps.payments.selectors import get_enrollment_payment_status, get_student_payment_summary
from apps.progression.constants import (
    CERTIFICATE_STATUS_AVAILABLE,
    REJOIN_STATUS_ACTIVE,
)
from apps.progression.models import (
    FinalExamEligibility,
    PromotionDecision,
    SemesterEvaluationConfig,
    SemesterRejoinPlan,
    StudentDiplomaCompletion,
    StudentGradeItem,
    StudentSemesterProgress,
)
from apps.students.services import check_apply_eligibility

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.students.models import StudentProfile


ALL_SECTIONS = [
    "identity", "applications", "intake_requests", "diploma_enrollments", "course_enrollments",
    "active_courses", "expired_courses", "exam_assignments",
    "assessment_attempts", "offline_exam_records", "homework_submissions", "semester_progress",
    "promotion_decisions", "completions", "certificates", "eligibilities", "apply",
    "payments", "notifications", "grade_items",
    "attendance", "open_assessments", "actionable_attempts", "record_history",
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
    "offline_exam_records": ("offline_exam_records", lambda p, u: _get_offline_exam_records(u)),
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
    "actionable_attempts": ("actionable_attempts", lambda p, u: list(get_student_attempts(u))),
    "record_history": ("record_history", lambda p, u: _get_record_history(p)),
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

    # Batch: one active waiver lookup for every open application (P5-02).
    from apps.payments.constants import WAIVER_STATUS_ACTIVE
    from apps.payments.models import PaymentWaiver
    waived_app_ids = set(
        PaymentWaiver.objects.filter(
            application_id__in=[a.id for a in open_applications],
            status=WAIVER_STATUS_ACTIVE,
        ).values_list("application_id", flat=True)
    ) if open_applications else set()

    for application in open_applications:
        is_waived = application.id in waived_app_ids
        for request in application.document_requests.all():
            if request.status == DOCUMENT_REQUEST_STATUS_REQUESTED and request.expires_at > now:
                actions.append({
                    "kind": "document", "priority": "urgent" if (request.expires_at - now).days < 7 else "normal",
                    "title": _("Upload requested documents"),
                    "description": _("Waiting for your documents — open the upload from your applications."),
                    "url": reverse("applications:my_document_upload", args=[application.id]),
                    "cta_label": _("Upload documents"),
                    "due_at": request.expires_at, "blocked_reason": None,
                })
        for request in application.intake_requests.all():
            if request.status == "requested" and request.expires_at > now:
                actions.append({
                    "kind": "intake", "priority": "urgent" if (request.expires_at - now).days < 7 else "normal",
                    "title": _("Complete the intake form"),
                    "description": _("Waiting for your intake form — use the secure link you received by email."),
                    "url": reverse("students:applications"), "cta_label": _("Open applications"),
                    "due_at": request.expires_at, "blocked_reason": None,
                })
        if application.status == PROGRAM_APP_STATUS_PAYMENT_PENDING and not is_waived:
            actions.append({
                "kind": "payment", "priority": "urgent", "title": _("Complete application payment"),
                "description": _("Awaiting payment confirmation by the admissions office."),
                "url": reverse("students:applications"), "cta_label": _("Open applications"),
                "due_at": None, "blocked_reason": None,
            })
        elif application.status == PROGRAM_APP_STATUS_PAYMENT_PENDING and is_waived:
            actions.append({
                "kind": "payment", "priority": "normal", "title": _("Payment waived"),
                "description": _("Your payment was waived by the admissions office."),
                "url": reverse("students:applications"), "cta_label": _("Open applications"),
                "due_at": None, "blocked_reason": None,
            })

    for entry in data.get("exam_assignments", []):
        attempt = entry["attempt"]
        readiness = entry["readiness"]
        if readiness["state"] in ("ready", "in_progress") and readiness["action_url"]:
            actions.append({
                "kind": "entrance_exam", "priority": "urgent", "title": entry["exam"].title,
                "description": _("Your entrance exam is ready."),
                "url": readiness["action_url"],
                "cta_label": readiness["action_label"], "due_at": None, "blocked_reason": None,
            })

    user = data["user"]
    all_attempts = data.get("actionable_attempts")
    if all_attempts is None:
        all_attempts = list(get_student_attempts(user))
        data["actionable_attempts"] = all_attempts
    attempted_ids = {attempt.assessment_id for attempt in all_attempts}
    actionable_assessment_count = sum(
        attempt.status in {"assigned", "in_progress"} for attempt in all_attempts
    )
    for attempt in all_attempts:
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

    diploma_cards = []
    enrollments = _get_student_visible_diploma_enrollments(data.get("diploma_enrollments", []))
    student = data["student"]
    lectures_by_cohort = {}
    progress_by_enrollment = {}
    if enrollments:
        now = timezone.now()
        cohort_ids = [e.cohort_id for e in enrollments]
        for lec in DiplomaLecture.objects.filter(
            cohort_id__in=cohort_ids, is_published=True,
        ).filter(
            Q(release_at__isnull=True) | Q(release_at__lte=now),
        ).order_by("order").select_related("semester"):
            lectures_by_cohort.setdefault(lec.cohort_id, []).append(lec)

        all_lecture_ids = [lec.id for lecs in lectures_by_cohort.values() for lec in lecs]
        if all_lecture_ids:
            enrollment_ids = [e.id for e in enrollments]
            for eid, lid in DiplomaLectureWatchProgress.objects.filter(
                student=student, diploma_enrollment_id__in=enrollment_ids,
                lecture_id__in=all_lecture_ids, status="completed",
            ).values_list("diploma_enrollment_id", "lecture_id"):
                progress_by_enrollment.setdefault(eid, set()).add(lid)

    visible_sem_ids_by_enrollment = get_visible_semester_ids_by_enrollment(enrollments)

    for enrollment in enrollments:
        if enrollment.status != ENROLLMENT_STATUS_ACTIVE:
            lectures = []
        elif enrollment.expires_at and timezone.now() >= enrollment.expires_at:
            lectures = []
        else:
            all_lectures = lectures_by_cohort.get(enrollment.cohort_id, [])
            visible_ids = visible_sem_ids_by_enrollment.get(enrollment.id, set())
            lectures = [lec for lec in all_lectures if lec.semester_id in visible_ids] if visible_ids else []

        completed_ids = set()
        if lectures:
            completed_ids = progress_by_enrollment.get(enrollment.id, set()) & {lec.id for lec in lectures}
        next_lecture = next((lec for lec in lectures if lec.id not in completed_ids), None)
        progress = diploma_progress_for_enrollment(student, enrollment)
        diploma_attempt = next((attempt for attempt in all_attempts if attempt.status in {"assigned", "in_progress"} and attempt.assessment.cohort_id == enrollment.cohort_id), None)
        if not diploma_attempt and enrollment.cohort_id in open_by_cohort:
            a = open_by_cohort[enrollment.cohort_id]
            if a.id not in attempted_ids:
                diploma_attempt = SimpleNamespace(assessment=a)
        diploma_cards.append({
            "type": "diploma", "title": enrollment.cohort.diploma.title,
            "status": enrollment.status, "status_label": enrollment.get_status_display(),
            "enrollment_id": enrollment.id,
            "detail_url": reverse("academics:student_diploma_detail", args=[enrollment.id]),
            "can_open": enrollment.status == ENROLLMENT_STATUS_ACTIVE and (
                enrollment.expires_at is None or enrollment.expires_at > timezone.now()
            ),
            "expires_at": enrollment.expires_at,
            "lecture_count": progress["total_lectures"], "completed_count": progress["completed_count"],
            "progress_percent": progress["percent"],
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
            "active_learning_count": len(data.get("active_courses", [])) + len([
                e for e in enrollments
                if e.status == ENROLLMENT_STATUS_ACTIVE
                and (e.expires_at is None or e.expires_at > timezone.now())
            ]),
            "actionable_assessment_count": actionable_assessment_count,
            "open_entrance_exam_count": sum(
                entry["readiness"]["state"] in ("ready", "in_progress")
                for entry in data.get("exam_assignments", [])
            ),
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


def _get_student_visible_diploma_enrollments(enrollments: list[DiplomaEnrollment]) -> list[DiplomaEnrollment]:
    """Return enrollments that represent the student's current learning view.

    A rejoin keeps the source enrollment as history, but the active target is
    the only enrollment that may appear in student learning. Restricted rows
    never expose a second, non-actionable learning card.
    """
    if not enrollments:
        return []
    student_id = enrollments[0].student_id
    active_rejoin_sources = set(
        SemesterRejoinPlan.objects.filter(
            student_id=student_id,
            status=REJOIN_STATUS_ACTIVE,
        ).values_list("from_enrollment_id", flat=True)
    )
    return [
        enrollment for enrollment in enrollments
        if enrollment.status != ENROLLMENT_STATUS_RESTRICTED
        and enrollment.id not in active_rejoin_sources
    ]


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
        CourseVersion.objects.filter(
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
    cards = []
    for cv in versions:
        enrollment = enrollments_map.get(cv.pk)
        progress = course_progress_for_enrollment(student_profile, cv)
        cards.append({
            "course_version": cv,
            "course": cv.course,
            "detail_url": reverse("academics:student_course_detail", args=[cv.pk]),
            "can_open": True,
            "lecture_count": progress["total_lectures"],
            "completed_count": progress["completed_count"],
            "progress_percent": progress["percent"],
            "expires_at": enrollment.expires_at if enrollment else None,
            "status": ENROLLMENT_STATUS_ACTIVE,
            "status_label": enrollment.get_status_display() if enrollment else _("Active"),
            "next_lecture": next_lectures.get(cv.pk),
            "next_lecture_url": reverse("academics:student_lecture_view", args=[cv.pk, next_lectures[cv.pk].pk]) if cv.pk in next_lectures else None,
            "next_assessment": next_assessments.get(cv.pk),
            "next_assessment_url": reverse("assessments:instructions", args=[next_assessments[cv.pk].assessment_id]) if cv.pk in next_assessments else None,
        })
    return cards


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
            "status_label": e.get_status_display(),
        }
        for e in qs
    ]


# ---------------------------------------------------------------------------
# Entrance exam assignments
# ---------------------------------------------------------------------------


def _exam_readiness(attempt: EntranceAttempt, now=None) -> dict:
    """Project one entrance attempt into a student-facing state (E2).

    Returns ``state`` (ready / in_progress / scheduled / closed / completed),
    a translated ``state_label``, a ``decision`` dict (None until the admin
    decides), and an ``action_url``/``action_label`` pair that only exists when
    the student can act right now. Grades are never exposed: a submitted or
    graded attempt offers no action until the admin makes a decision.
    """
    now = now or timezone.now()
    exam = attempt.exam
    access_url = reverse("entrance_exams:exam_access_token", args=[exam.access_token]) if exam.access_token else None
    decision = attempt_decision(attempt)
    # ``submitted_at`` is the durable "has taken the exam" marker. A submitted
    # attempt is final even if a stale row still carries an in-progress status:
    # it must never offer an exam action again.
    if attempt.submitted_at or attempt.status in (ATTEMPT_STATUS_COMPLETED, ATTEMPT_STATUS_GRADED):
        if decision:
            state_label = decision["label"]
            tone = decision["tone"]
        else:
            state_label = _("Submitted — awaiting review")
            tone = "info"
        return {"state": "completed", "state_label": state_label, "decision": decision, "tone": tone, "action_url": None, "action_label": None}
    if attempt.status == ATTEMPT_STATUS_IN_PROGRESS:
        return {"state": "in_progress", "state_label": _("In progress"), "decision": None, "tone": "info", "action_url": access_url, "action_label": _("Continue exam")}
    if attempt.program_application.status == PROGRAM_APP_STATUS_REJECTED:
        return {"state": "closed", "state_label": _("Closed"), "decision": None, "tone": "neutral", "action_url": None, "action_label": None}
    if not exam.is_active:
        return {"state": "closed", "state_label": _("Closed"), "decision": None, "tone": "neutral", "action_url": None, "action_label": None}
    if exam.open_at and now < exam.open_at:
        return {"state": "scheduled", "state_label": _("Scheduled"), "decision": None, "tone": "info", "action_url": None, "action_label": None}
    if exam.close_at and now > exam.close_at:
        return {"state": "closed", "state_label": _("Closed"), "decision": None, "tone": "neutral", "action_url": None, "action_label": None}
    return {"state": "ready", "state_label": _("Ready"), "decision": None, "tone": "info", "action_url": access_url, "action_label": _("Open exam")}


def _get_exam_assignments(profile) -> list[dict]:
    attempts = EntranceAttempt.objects.filter(student=profile).select_related(
        "exam", "graded_by", "program_application",
    ).order_by("-assigned_at")
    return [
        {
            "attempt": attempt,
            "exam": attempt.exam,
            "assigned_at": attempt.assigned_at,
            "status": attempt.status,
            "status_label": attempt.get_status_display(),
            "readiness": _exam_readiness(attempt),
        }
        for attempt in attempts
    ]


def get_application_row(application: ProgramApplication) -> dict:
    """Project one application into a status-focused portal row (A1/A2).

    Only server-derivable safe actions are exposed: an open entrance exam or
    its result link. Document, intake, and payment steps show a clear pending
    state instead of a dead CTA, because their secure tokens are stored hashed
    and cannot be reconstructed from the portal. Payment readiness is shown
    distinctly: paid, waived (Admin waiver), or awaiting payment.
    """
    from apps.applications.services import has_active_application_waiver, has_application_paid_record

    now = timezone.now()
    is_paid = has_application_paid_record(application)
    is_waived = has_active_application_waiver(application)
    if is_paid:
        payment_state = "paid"
    elif is_waived:
        payment_state = "waived"
    else:
        payment_state = "pending"
    row = {
        "program_title": (
            application.cohort.diploma.title if application.cohort_id and application.cohort.diploma_id
            else getattr(application.course, "title", _("Program application"))
        ),
        "program_type_label": application.get_program_type_display(),
        "submitted_at": application.created_at,
        "status": application.status,
        "status_label": application.get_status_display(),
        "payment_state": payment_state,
        "documents_count": application.document_requests.count(),
        "next_step": None,
        "action_url": None,
        "action_label": None,
    }

    if application.status == PROGRAM_APP_STATUS_INVITED:
        attempt = application.entrance_attempts.select_related("exam").first()
        exam = attempt.exam if attempt else None
        if attempt and attempt.submitted_at:
            # The student already took the exam: never offer it again.
            row["next_step"] = _("Your entrance exam has been submitted. Awaiting the admissions office decision.")
        elif exam and exam.is_active and exam.access_token:
            if (not exam.open_at or exam.open_at <= now) and (not exam.close_at or exam.close_at > now):
                row["action_url"] = reverse("entrance_exams:exam_access_token", args=[exam.access_token])
                row["action_label"] = _("Open exam")
            else:
                row["next_step"] = _("Your entrance assessment is scheduled. Use the link you received by email when it opens.")
        else:
            row["next_step"] = _("Your entrance assessment has not been assigned yet.")
    elif application.status == PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED:
        row["next_step"] = _("Your entrance exam has been submitted. Awaiting the admissions office decision.")

    if application.status == PROGRAM_APP_STATUS_PAYMENT_PENDING:
        if payment_state == "waived":
            row["next_step"] = _("Payment waived by the admissions office.")
        elif payment_state == "paid":
            row["next_step"] = _("Payment confirmed.")
        else:
            row["next_step"] = _("Awaiting payment confirmation by the admissions office.")

    doc_request = application.document_requests.filter(
        status=DOCUMENT_REQUEST_STATUS_REQUESTED, expires_at__gt=now,
    ).first()
    if doc_request:
        row["action_url"] = reverse("applications:my_document_upload", args=[application.id])
        row["action_label"] = _("Upload documents")
        row["next_step"] = _("Upload requested documents")

    intake_request = application.intake_requests.filter(
        status="requested", expires_at__gt=now,
    ).first()
    if intake_request:
        row["next_step"] = _("Waiting for your intake form — use the secure link you received by email.")

    return row


# ---------------------------------------------------------------------------
# Normal assessment attempts
# ---------------------------------------------------------------------------


def _get_assessment_attempts(user: User) -> list[AssessmentAttempt]:
    """Return a safe profile projection for submitted assessment attempts."""
    attempts = (
        get_student_attempts(user)
        .filter(submitted_at__isnull=False)
        .select_related("assessment__course_version__course")[:50]
    )
    return [
        SimpleNamespace(
            id=attempt.id,
            assessment=attempt.assessment,
            status=attempt.status,
            status_label=attempt.get_status_display(),
            submitted_at=attempt.submitted_at,
            visible_to_student=bool(attempt.visible_to_student),
            score_percent=attempt.score_percent if attempt.visible_to_student else None,
            result_url=reverse("assessments:results", args=[attempt.assessment_id]) if attempt.visible_to_student else None,
        )
        for attempt in attempts
    ]


# ---------------------------------------------------------------------------
# Offline exam records (recorded section grading, no questions)
# ---------------------------------------------------------------------------


def _get_offline_exam_records(user: User) -> list[SimpleNamespace]:
    """Project the student's offline Semester Exam codes as grading records.

    Each record shows the anonymous code, per-section grading state
    (structure only — never questions), and the computed result only after
    Admin release. Section scores stay hidden until release.
    """
    codes = list(
        SemesterExamCode.objects.filter(
            active=True,
            delivery_mode=assessment_constants.DELIVERY_MODE_OFFLINE,
            diploma_enrollment__student__user=user,
        )
        .select_related("assessment")
        .order_by("-created_at")[:50]
    )
    if not codes:
        return []
    assessment_ids = {code.assessment_id for code in codes}
    sections_by_assessment: dict[int, list] = {}
    for section in AssessmentLectureSection.objects.filter(
        assessment_id__in=assessment_ids,
    ).select_related("lecture").order_by("order", "id"):
        sections_by_assessment.setdefault(section.assessment_id, []).append(section)
    grades_by_code: dict[int, dict[int, Decimal]] = {}
    for grade in OfflineSectionGrade.objects.filter(exam_code__in=codes):
        grades_by_code.setdefault(grade.exam_code_id, {})[grade.section_id] = grade.score
    attempts_by_assessment = {
        attempt.assessment_id: attempt
        for attempt in AssessmentAttempt.objects.filter(
            student=user,
            assessment_id__in=assessment_ids,
            status__in=[
                assessment_constants.ATTEMPT_STATUS_GRADED,
                assessment_constants.ATTEMPT_STATUS_RELEASED,
            ],
        ).order_by("-created_at")
    }
    records = []
    for code in codes:
        sections = sections_by_assessment.get(code.assessment_id, [])
        grades = grades_by_code.get(code.id, {})
        graded_count = sum(1 for section in sections if section.id in grades)
        complete = bool(sections) and graded_count == len(sections)
        attempt = attempts_by_assessment.get(code.assessment_id)
        released = bool(attempt and attempt.visible_to_student)
        if released:
            status = attempt.status
            status_label = attempt.get_status_display()
        elif complete:
            status = "graded"
            status_label = _("Graded — awaiting release")
        elif graded_count:
            status = "under_review"
            status_label = _("Under review")
        else:
            status = "assigned"
            status_label = _("Awaiting grading")
        records.append(
            SimpleNamespace(
                id=code.id,
                assessment=code.assessment,
                code_number=code.code_number,
                status=status,
                status_label=status_label,
                graded_count=graded_count,
                section_count=len(sections),
                released=released,
                score_percent=attempt.score_percent if released else None,
                result_url=reverse("assessments:results", args=[code.assessment_id]) if released else None,
                sections=[
                    SimpleNamespace(
                        id=section.id,
                        label=str(section.lecture),
                        max_grade=section.max_grade,
                        graded=section.id in grades,
                        score=grades.get(section.id) if released else None,
                    )
                    for section in sections
                ],
            )
        )
    return records


# ---------------------------------------------------------------------------
# Homework submissions
# ---------------------------------------------------------------------------


def _get_homework_submissions(user: User) -> list[HomeworkSubmission]:
    return list(
        HomeworkSubmission.objects.filter(student=user)
        .select_related("assessment__course_version__course", "reviewed_by", "attempt")
        .prefetch_related("attachments")
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
    # The card percentage is the canonical evaluation-equivalent percentage
    # (DECISION §13), not a record-based all-time count; the record list and
    # counts stay the recent attendance history display.
    return {
        "records": records,
        "counts": counts,
        "total": total,
        "attendance_percent": _canonical_attendance_percent(student_profile),
    }


def _canonical_attendance_percent(student_profile: StudentProfile) -> Decimal:
    """Canonical attendance percentage for the record card (DECISION §13).

    Rolls up the per (enrollment, visible semester) evaluation-equivalent
    percentage — required-count capped when the cohort/semester config defines
    it, otherwise held-session — weighted by each semester's denominator, so
    the student sees the same percentage the evaluation uses.
    """
    enrollments = _get_student_visible_diploma_enrollments(list(
        student_profile.diploma_enrollments.filter(status=ENROLLMENT_STATUS_ACTIVE)
        .select_related("cohort")
    ))
    pairs_by_semester: dict[int, list[DiplomaEnrollment]] = {}
    semester_by_id: dict[int, Semester] = {}
    cohort_ids: set[int] = set()
    for enrollment in enrollments:
        visible_semesters = get_student_diploma_access(enrollment)["visible_semesters"]
        for semester in visible_semesters:
            pairs_by_semester.setdefault(semester.id, []).append(enrollment)
            semester_by_id[semester.id] = semester
            cohort_ids.add(enrollment.cohort_id)

    if not semester_by_id:
        return Decimal("0")

    required_by_key: dict[tuple[int, int], int] = {
        (config.cohort_id, config.semester_id): config.attendance_required_sessions
        for config in SemesterEvaluationConfig.objects.filter(
            cohort_id__in=cohort_ids,
            semester_id__in=list(semester_by_id),
        )
    }

    total_percent = Decimal("0")
    total_weight = 0
    for semester_id, enrollment_group in pairs_by_semester.items():
        semester = semester_by_id[semester_id]
        summaries_by_enrollment = get_attendance_summaries(enrollment_group, semester)
        for enrollment in enrollment_group:
            summary = summaries_by_enrollment.get(enrollment.id)
            if summary is None or not summary["total_sessions"]:
                continue
            required = required_by_key.get((enrollment.cohort_id, semester_id))
            percent = get_attendance_evaluation_percent(
                enrollment, semester, required, summary=summary,
            )
            if percent is None:
                continue
            basis = required if (required and required > 0) else summary["total_sessions"]
            total_percent += percent * Decimal(basis)
            total_weight += basis

    if total_weight <= 0:
        return Decimal("0")
    return (total_percent / Decimal(total_weight)).quantize(Decimal("0.01"))


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
# Record history (P5-03)
# ---------------------------------------------------------------------------


def _get_record_history(student_profile: StudentProfile) -> dict:
    """Immutable academic-history projection for the Record hub.

    Composes exact enrollment history (each cohort stays a separate record),
    immutable calculated Semester Evaluations, and authoritative final
    progression outcomes — calculated result shown separately from the final
    decision, with Interview Pending kept neutral and exceptional decisions
    clearly marked. Never merges/copies grades or attendance across enrollments.
    """
    from apps.progression.constants import FINAL_PROGRESSION_STATUS_INTERVIEW_PENDING
    from apps.progression.models import (
        FinalProgressionOutcome,
        SemesterEvaluation,
        SemesterInterview,
        SemesterRejoinPlan,
    )

    enrollments = list(
        DiplomaEnrollment.objects.filter(student=student_profile)
        .select_related("cohort__diploma", "entry_semester")
        .order_by("-created_at")
    )
    enrollment_ids = [e.id for e in enrollments]
    if not enrollment_ids:
        return {"enrollments": [], "semester_rows": [], "rejoins": []}

    evaluations = {
        (ev.diploma_enrollment_id, ev.semester_id): ev
        for ev in SemesterEvaluation.objects.filter(
            student=student_profile,
            diploma_enrollment_id__in=enrollment_ids,
        ).select_related("semester")
    }
    outcomes = {
        outcome.evaluation_id: outcome
        for outcome in FinalProgressionOutcome.objects.filter(
            student=student_profile,
            diploma_enrollment_id__in=enrollment_ids,
        ).select_related("semester")
    }
    interviews = {
        interview.outcome_id: interview
        for interview in SemesterInterview.objects.select_related("outcome").filter(
            outcome__student=student_profile,
            outcome__diploma_enrollment_id__in=enrollment_ids,
        )
    }
    rejoins = list(
        SemesterRejoinPlan.objects.filter(
            student=student_profile,
            from_enrollment_id__in=enrollment_ids,
        ).select_related("from_enrollment__cohort__diploma", "to_enrollment__cohort__diploma", "failed_semester", "target_cohort__diploma")
        .order_by("-created_at")
    )

    # Student-facing enrollment preview uses the effective enrollment only. The
    # source row and its academic history remain queryable in semester_rows.
    enrollment_rows = []
    visible_enrollment_ids = {
        enrollment.id
        for enrollment in _get_student_visible_diploma_enrollments(enrollments)
    }
    for enrollment in enrollments:
        if enrollment.id not in visible_enrollment_ids:
            continue
        rejoin_from = next((r for r in rejoins if r.from_enrollment_id == enrollment.id), None)
        rejoin_to = next((r for r in rejoins if r.to_enrollment_id == enrollment.id), None)
        enrollment_rows.append({
            "enrollment": enrollment,
            "cohort": enrollment.cohort,
            "diploma": enrollment.cohort.diploma,
            "entry_semester": enrollment.entry_semester,
            "status": enrollment.status,
            "status_label": enrollment.get_status_display(),
            "rejoin_from": rejoin_from,
            "rejoin_to": rejoin_to,
            "semesters": [],
        })

    semester_rows = []
    for (enrollment_id, _semester_id), evaluation in evaluations.items():
        outcome = outcomes.get(evaluation.id)
        interview = interviews.get(outcome.id) if outcome else None
        row = {
            "enrollment_id": enrollment_id,
            "semester": evaluation.semester,
            "calculated_result": evaluation.result,
            "calculated_label": evaluation.get_result_display(),
            "overall_percent": evaluation.overall_percent,
            "attendance_percent": evaluation.attendance_percent,
            "homework_percent": evaluation.homework_percent,
            "semester_exam_percent": evaluation.semester_exam_percent,
            "finalized_at": evaluation.finalized_at,
            "outcome": None,
        }
        if outcome:
            final_label = outcome.get_final_result_display() if outcome.final_result else _("Not decided")
            row["outcome"] = {
                "status": outcome.status,
                "status_label": outcome.get_status_display(),
                "is_interview_pending": outcome.status == FINAL_PROGRESSION_STATUS_INTERVIEW_PENDING,
                "final_result": outcome.final_result,
                "final_label": final_label,
                "is_exceptional": bool(outcome.is_exceptional),
                "reason": outcome.reason,
                "decided_at": outcome.decided_at,
                "interview_status": interview.get_status_display() if interview else None,
            }
        semester_rows.append(row)

    semester_rows.sort(key=lambda row: (row["enrollment_id"], row["semester"].order))

    return {
        "enrollments": enrollment_rows,
        "semester_rows": semester_rows,
        "rejoins": rejoins,
    }


# ---------------------------------------------------------------------------
# Promotion decisions
# ---------------------------------------------------------------------------


def _get_promotion_decisions(student_profile: StudentProfile) -> list[PromotionDecision]:
    decisions = list(
        PromotionDecision.objects.filter(student=student_profile)
        .select_related("from_semester", "to_semester", "decided_by")
        .order_by("-decided_at", "-id")[:50]
    )

    # The student projection shows only the latest administrative action for
    # each enrollment/starting-semester scope. Older rows remain untouched and
    # are still available to staff through the audit/progression views.
    latest_by_scope = {}
    for decision in decisions:
        if decision.diploma_enrollment_id or decision.from_semester_id:
            scope = (decision.diploma_enrollment_id, decision.from_semester_id)
        else:
            # Keep legacy unscoped rows independent rather than hiding them all
            # behind one shared ``(None, None)`` key.
            scope = ("legacy", decision.id)
        latest_by_scope.setdefault(scope, decision)
    return list(latest_by_scope.values())


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
    # Payment data is authoritative: an infrastructure failure must surface as
    # an error, never as a false "nothing paid" (S2-23).
    summary = get_student_payment_summary(student_profile)
    enrollments = get_enrollment_payment_status(student_profile)
    summary.update(enrollments)
    return summary


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
