"""Business logic services for assessments, scoring, attempts, and grading."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import CourseEnrollment, DiplomaEnrollment
from apps.accounts.constants import GROUP_ADMIN, GROUP_MODERATOR, GROUP_SUPER_ADMIN, GROUP_TEACHER
from apps.storage.services import storage_service

from .constants import (
    ASSESSMENT_TYPE_HOMEWORK,
    ATTEMPT_STATUS_CANCELLED,
    ATTEMPT_STATUS_EXPIRED,
    ATTEMPT_STATUS_GRADED,
    ATTEMPT_STATUS_IN_PROGRESS,
    ATTEMPT_STATUS_RELEASED,
    ATTEMPT_STATUS_SUBMITTED,
    ATTEMPT_STATUS_UNDER_REVIEW,
    HOMEWORK_STATUS_GRADED,
    HOMEWORK_STATUS_UNDER_REVIEW,
    HOMEWORK_STATUS_UPLOADED,
    QUESTION_TYPE_MCQ,
    QUESTION_TYPE_WRITTEN,
)
from .models import (
    Assessment,
    AssessmentAnswer,
    AssessmentAttempt,
    AssessmentChoice,
    AssessmentQuestion,
    HomeworkSubmission,
)

if TYPE_CHECKING:
    pass

HOMEWORK_ALLOWED_EXTENSIONS = [".pdf", ".docx"]


@dataclass
class AssessmentAnswerData:
    question_id: int
    selected_choice_id: int | None = None
    written_text: str = ""


@dataclass
class GradingResult:
    answer_id: int
    score: Decimal
    feedback: str


# ---------------------------------------------------------------------------
# Access & enrollment checks
# ---------------------------------------------------------------------------


def has_active_enrollment(student, assessment: Assessment) -> bool:
    """Check student has active enrollment for the assessment's course_version."""
    cv = assessment.course_version
    if not cv:
        return True
    if cv.cohort:
        return DiplomaEnrollment.objects.filter(
            student__user=student,
            cohort=cv.cohort,
            status=ENROLLMENT_STATUS_ACTIVE,
        ).exists()
    return CourseEnrollment.objects.filter(
        student__user=student,
        course_version=cv,
        status=ENROLLMENT_STATUS_ACTIVE,
    ).exists()


def check_assessment_access(student, assessment: Assessment) -> None:
    """Raise ValueError if the student cannot access the assessment."""
    now = timezone.now()
    if not assessment.is_active:
        raise ValueError("Assessment is not active.")
    if assessment.open_at and now < assessment.open_at:
        raise ValueError("Assessment has not opened yet.")
    if assessment.close_at and now > assessment.close_at:
        raise ValueError("Assessment has already closed.")
    if not has_active_enrollment(student, assessment):
        raise ValueError("You do not have an active enrollment for this course.")
    attempt_count = AssessmentAttempt.objects.filter(
        assessment=assessment, student=student,
    ).exclude(
        status__in=[ATTEMPT_STATUS_CANCELLED, ATTEMPT_STATUS_EXPIRED],
    ).count()
    if attempt_count >= assessment.max_attempts:
        raise ValueError("You have reached the maximum number of attempts.")


# ---------------------------------------------------------------------------
# Attempt lifecycle
# ---------------------------------------------------------------------------


def start_assessment(student, assessment: Assessment) -> AssessmentAttempt:
    check_assessment_access(student, assessment)
    existing = AssessmentAttempt.objects.filter(
        assessment=assessment,
        student=student,
        status=ATTEMPT_STATUS_IN_PROGRESS,
    ).first()
    if existing:
        return existing
    attempt_number = AssessmentAttempt.objects.filter(
        assessment=assessment, student=student,
    ).count() + 1
    if attempt_number > assessment.max_attempts:
        raise ValueError("You have reached the maximum number of attempts.")
    attempt = AssessmentAttempt.objects.create(
        assessment=assessment,
        student=student,
        attempt_number=attempt_number,
        status=ATTEMPT_STATUS_IN_PROGRESS,
        started_at=timezone.now(),
    )
    return attempt


def submit_assessment(attempt: AssessmentAttempt, answers: list[AssessmentAnswerData]) -> AssessmentAttempt:
    if attempt.status != ATTEMPT_STATUS_IN_PROGRESS:
        raise ValueError("Assessment can only be submitted from in_progress status.")
    assessment = attempt.assessment
    now = timezone.now()
    if not assessment.is_active:
        raise ValueError("Assessment is not active.")
    if assessment.close_at and now > assessment.close_at and not assessment.allow_late_submission:
        raise ValueError("Assessment has already closed.")
    if attempt.started_at and assessment.duration_minutes:
        deadline = attempt.started_at + timezone.timedelta(minutes=assessment.duration_minutes)
        if now > deadline:
            raise ValueError("Assessment duration has expired.")

    questions = list(assessment.questions.select_related("assessment").prefetch_related("choices"))
    question_map = {q.id: q for q in questions}
    answered_question_ids = {answer_data.question_id for answer_data in answers}
    missing_required = [q for q in questions if q.required and q.id not in answered_question_ids]
    if missing_required:
        raise ValueError("Please answer all required questions before submitting.")

    has_ungraded = False
    total_score = Decimal("0.00")
    total_possible = sum(q.points for q in questions)

    with transaction.atomic():
        for answer_data in answers:
            question = question_map.get(answer_data.question_id)
            if not question:
                continue
            if question.question_type == QUESTION_TYPE_MCQ:
                if answer_data.written_text:
                    raise ValueError("MCQ answer must not carry written text.")
                choice = _resolve_choice(question, answer_data.selected_choice_id)
                score = _score_mcq_answer(choice, question, assessment)
                AssessmentAnswer.objects.update_or_create(
                    attempt=attempt, question=question,
                    defaults={
                        "selected_choice": choice,
                        "written_text": "",
                        "score": score,
                        "feedback": "",
                    },
                )
                total_score += score
            elif question.question_type == QUESTION_TYPE_WRITTEN:
                if answer_data.selected_choice_id is not None:
                    raise ValueError("Written answer must not carry a selected choice.")
                AssessmentAnswer.objects.update_or_create(
                    attempt=attempt, question=question,
                    defaults={
                        "selected_choice": None,
                        "written_text": answer_data.written_text,
                        "score": None,
                        "feedback": "",
                    },
                )
                has_ungraded = True
        status = ATTEMPT_STATUS_UNDER_REVIEW if has_ungraded else ATTEMPT_STATUS_SUBMITTED
        attempt.status = status
        attempt.submitted_at = now
        attempt.score_points = total_score
        if total_possible > 0:
            attempt.score_percent = (total_score / total_possible) * Decimal("100.00")
        attempt.save(
            update_fields=[
                "status", "submitted_at", "score_points", "score_percent",
            ],
        )
    return attempt


def _resolve_choice(question: AssessmentQuestion, choice_id: int | None) -> AssessmentChoice | None:
    if choice_id is None:
        return None
    return AssessmentChoice.objects.filter(id=choice_id, question=question).first()


def _score_mcq_answer(
    choice: AssessmentChoice | None,
    question: AssessmentQuestion,
    assessment: Assessment,
) -> Decimal:
    if choice is None:
        return Decimal("0.00")
    if choice.is_correct:
        return question.points
    if assessment.penalty_percent > 0:
        penalty = question.points * (assessment.penalty_percent / Decimal("100.00"))
        return max(Decimal("0.00"), question.points - penalty)
    return Decimal("0.00")


# ---------------------------------------------------------------------------
# Homework submission
# ---------------------------------------------------------------------------


def submit_homework(
    student,
    assessment: Assessment,
    file_data: bytes,
    original_filename: str,
    content_type: str,
) -> HomeworkSubmission:
    """Submit a homework file, creating an attempt and storage record."""
    if assessment.assessment_type != ASSESSMENT_TYPE_HOMEWORK:
        raise ValueError("This assessment does not accept homework uploads.")
    now = timezone.now()
    if not assessment.is_active:
        raise ValueError("Assessment is not active.")
    if assessment.open_at and now < assessment.open_at:
        raise ValueError("Assessment has not opened yet.")
    if assessment.close_at and now > assessment.close_at and not assessment.allow_late_submission:
        raise ValueError("Assessment has already closed.")
    if not has_active_enrollment(student, assessment):
        raise ValueError("You do not have an active enrollment for this course.")
    if HomeworkSubmission.objects.filter(assessment=assessment, student=student).exists():
        raise ValueError("Homework has already been submitted.")
    attempt_count = AssessmentAttempt.objects.filter(
        assessment=assessment, student=student,
    ).exclude(
        status__in=[ATTEMPT_STATUS_CANCELLED, ATTEMPT_STATUS_EXPIRED],
    ).count()
    if attempt_count >= assessment.max_attempts:
        raise ValueError("You have reached the maximum number of attempts.")
    max_size = settings.HOMEWORK_MAX_UPLOAD_MB * 1024 * 1024
    result = storage_service.upload_file(
        data=file_data,
        key_prefix=f"homework/{assessment.id}",
        content_type=content_type,
        max_size=max_size,
        allowed_types=HOMEWORK_ALLOWED_EXTENSIONS,
        original_filename=original_filename,
    )
    attempt = AssessmentAttempt.objects.create(
        assessment=assessment,
        student=student,
        attempt_number=AssessmentAttempt.objects.filter(
            assessment=assessment, student=student,
        ).count() + 1,
        status=ATTEMPT_STATUS_SUBMITTED,
        submitted_at=timezone.now(),
    )
    submission = HomeworkSubmission.objects.create(
        assessment=assessment,
        student=student,
        attempt=attempt,
        original_filename=original_filename,
        storage_key=result.key,
        content_type=content_type,
        file_size=result.byte_size,
        checksum=result.checksum,
        status=HOMEWORK_STATUS_UNDER_REVIEW,
        submitted_at=timezone.now(),
    )
    return submission


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


def grade_written_answer(
    answer_id: int,
    score: Decimal,
    feedback: str,
    grader,
    *,
    override_reason: str = "",
) -> AssessmentAnswer:
    answer = AssessmentAnswer.objects.select_related("attempt__assessment").get(id=answer_id)
    if score < 0:
        raise ValueError("Score cannot be negative.")
    if answer.question and score > answer.question.points:
        raise ValueError(
            f"Score ({score}) cannot exceed question points ({answer.question.points})."
        )
    is_override = answer.score is not None and answer.score != score
    answer.score = score
    answer.feedback = feedback
    answer.graded_at = timezone.now()
    answer.graded_by = grader
    if is_override:
        if not override_reason:
            raise ValueError("An override reason is required when changing a previously-graded answer.")
        answer.score_override_reason = override_reason
    answer.save(update_fields=["score", "feedback", "graded_at", "graded_by", "score_override_reason"])
    _update_attempt_grade(answer.attempt)
    return answer


def grade_homework(submission_id: int, score: Decimal, feedback: str, grader) -> HomeworkSubmission:
    submission = HomeworkSubmission.objects.select_related("assessment").get(id=submission_id)
    submission.score = score
    submission.feedback = feedback
    submission.reviewed_at = timezone.now()
    submission.reviewed_by = grader
    submission.status = HOMEWORK_STATUS_GRADED
    submission.save(update_fields=["score", "feedback", "reviewed_at", "reviewed_by", "status"])
    return submission


def _update_attempt_grade(attempt: AssessmentAttempt):
    total = Decimal("0.00")
    all_graded = True
    for answer in attempt.answers.select_related("question").all():
        if answer.score is not None:
            total += answer.score
        else:
            all_graded = False
    attempt.score_points = total
    assessment = attempt.assessment
    total_possible = sum(q.points for q in assessment.questions.all())
    if total_possible > 0:
        attempt.score_percent = (total / total_possible) * Decimal("100.00")
    if all_graded:
        attempt.status = ATTEMPT_STATUS_GRADED
        attempt.graded_at = timezone.now()
        attempt.is_pass = (
            attempt.score_percent >= assessment.passing_score
            if attempt.score_percent is not None else None
        )
        attempt.save(
            update_fields=[
                "score_points", "score_percent", "status",
                "graded_at", "is_pass",
            ],
        )
    else:
        attempt.save(update_fields=["score_points", "score_percent"])


# ---------------------------------------------------------------------------
# Grade release
# ---------------------------------------------------------------------------


def release_grades(attempt: AssessmentAttempt) -> AssessmentAttempt:
    if attempt.status != ATTEMPT_STATUS_GRADED:
        raise ValueError("Only graded attempts can be released.")
    attempt.status = ATTEMPT_STATUS_RELEASED
    attempt.visible_to_student = True
    attempt.save(update_fields=["status", "visible_to_student"])
    return attempt


def hide_grades(attempt: AssessmentAttempt) -> AssessmentAttempt:
    attempt.visible_to_student = False
    attempt.status = ATTEMPT_STATUS_GRADED
    attempt.save(update_fields=["visible_to_student", "status"])
    return attempt


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def get_student_assessments(student):
    """Return assessments visible to the student (enrolled + open)."""
    now = timezone.now()
    return (
        Assessment.objects.filter(
            is_active=True,
            open_at__lte=now,
        )
        .filter(
            Q(course_version__isnull=True)
            | Q(
                course_version__cohort__enrollments__student__user=student,
                course_version__cohort__enrollments__status=ENROLLMENT_STATUS_ACTIVE,
            )
            | Q(
                course_version__enrollments__student__user=student,
                course_version__enrollments__status=ENROLLMENT_STATUS_ACTIVE,
            )
        )
        .distinct()
        .select_related("course_version__course")
        .prefetch_related("attempts")
    )


def get_student_attempts(student):
    return AssessmentAttempt.objects.filter(student=student).select_related(
        "assessment",
    ).order_by("-created_at")


def get_pending_grading():
    return AssessmentAttempt.objects.filter(
        status=ATTEMPT_STATUS_UNDER_REVIEW,
    ).select_related("assessment", "student")


def get_homework_pending_grading():
    return HomeworkSubmission.objects.filter(
        status__in=[HOMEWORK_STATUS_UPLOADED, HOMEWORK_STATUS_UNDER_REVIEW],
    ).select_related("assessment", "student", "attempt")


def get_grading_queue(user):
    """Return attempts pending review, scoped to user's permissions."""
    if user.is_superuser or user.groups.filter(name=GROUP_SUPER_ADMIN).exists():
        return get_pending_grading()
    if user.groups.filter(name=GROUP_ADMIN).exists():
        return get_pending_grading()
    if user.groups.filter(name=GROUP_MODERATOR).exists():
        return get_pending_grading()
    if user.groups.filter(name=GROUP_TEACHER).exists():
        return get_pending_grading()
    return AssessmentAttempt.objects.none()
