"""Business logic services for assessments, scoring, attempts, and grading."""

from __future__ import annotations

import random
import secrets
import string
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import CourseEnrollment, DiplomaEnrollment
from apps.academics.services import get_visible_semester_ids_by_enrollment
from apps.audit.constants import (
    ACTION_EXAM_ACCESS_OVERRIDE_CANCELLED,
    ACTION_EXAM_ACCESS_OVERRIDE_CREATED,
    ACTION_EXAM_ACCESS_OVERRIDE_USED,
)
from apps.audit.services import record_audit_event
from apps.core.permissions import assessment_staff_scope_filter, is_admin, validate_score
from apps.storage.services import storage_service

from .constants import (
    ASSESSMENT_TYPE_HOMEWORK,
    ASSESSMENT_TYPE_SEMESTER_EXAM,
    ATTEMPT_STATUS_CANCELLED,
    ATTEMPT_STATUS_EXPIRED,
    ATTEMPT_STATUS_GRADED,
    ATTEMPT_STATUS_IN_PROGRESS,
    ATTEMPT_STATUS_RELEASED,
    ATTEMPT_STATUS_SUBMITTED,
    ATTEMPT_STATUS_UNDER_REVIEW,
    DELIVERY_MODE_OFFLINE,
    DELIVERY_MODE_ONLINE,
    EXAM_OVERRIDE_ACTIVE,
    EXAM_OVERRIDE_CANCELLED,
    EXAM_OVERRIDE_USED,
    HOMEWORK_STATUS_GRADED,
    HOMEWORK_STATUS_UNDER_REVIEW,
    HOMEWORK_STATUS_UPLOADED,
    QUESTION_TYPE_MCQ,
    QUESTION_TYPE_WRITTEN,
    SNAPSHOT_QUESTION_SOURCE_BANK,
    SNAPSHOT_QUESTION_SOURCE_MANUAL,
)
from .models import (
    Assessment,
    AssessmentAnswer,
    AssessmentAttempt,
    AssessmentAttemptChoice,
    AssessmentAttemptQuestion,
    AssessmentAttemptSnapshotAnswer,
    AssessmentChoice,
    AssessmentLectureSection,
    AssessmentQuestion,
    DiplomaLectureChoice,
    DiplomaLectureQuestion,
    HomeworkSubmission,
    OfflineSectionGrade,
    SemesterExamAccessOverride,
    SemesterExamCode,
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
    """Check student has active enrollment for the assessment's scope."""
    cv = assessment.course_version
    if cv:
        return CourseEnrollment.objects.filter(
            student__user=student,
            course_version=cv,
            status=ENROLLMENT_STATUS_ACTIVE,
        ).exists()
    # Diploma-scoped assessment (no course_version, has cohort+semester)
    if assessment.cohort and assessment.semester:
        return DiplomaEnrollment.objects.filter(
            student__user=student,
            cohort=assessment.cohort,
            status=ENROLLMENT_STATUS_ACTIVE,
        ).exists()
    return True


def check_delivery_mode(student, assessment: Assessment) -> None:
    """Raise ValueError if this is an offline semester exam for the student."""
    if assessment.assessment_type != ASSESSMENT_TYPE_SEMESTER_EXAM:
        return
    if assessment.cohort_id is None:
        return
    code = SemesterExamCode.objects.filter(
        assessment=assessment,
        diploma_enrollment__student__user=student,
        active=True,
    ).first()
    if code and code.delivery_mode == DELIVERY_MODE_OFFLINE:
        raise ValueError(_("This exam is offline-only and cannot be taken online."))


def check_assessment_access(student, assessment: Assessment) -> None:
    """Raise ValueError if the student cannot access the assessment."""
    now = timezone.now()
    if not assessment.is_active:
        raise ValueError(_("Assessment is not active."))
    if not has_active_enrollment(student, assessment):
        raise ValueError(_("You do not have an active enrollment for this course."))

    if assessment.cohort_id and assessment.semester_id:
        from apps.academics.services import get_student_diploma_access

        enrollment = DiplomaEnrollment.objects.filter(
            student__user=student,
            cohort_id=assessment.cohort_id,
            status=ENROLLMENT_STATUS_ACTIVE,
        ).first()
        if not enrollment or assessment.semester_id not in {
            semester.id for semester in get_student_diploma_access(enrollment)["visible_semesters"]
        }:
            raise ValueError(_("This semester is not available yet."))

    check_delivery_mode(student, assessment)

    is_semester_exam = (
        assessment.assessment_type == ASSESSMENT_TYPE_SEMESTER_EXAM
        and assessment.cohort_id and assessment.semester_id
        and assessment.course_version_id is None
    )
    if is_semester_exam:
        override = get_active_semester_exam_override(student, assessment, now)
        if override and override.open_at <= now <= override.close_at:
            attempt_count = AssessmentAttempt.objects.filter(
                assessment=assessment, student=student,
            ).exclude(
                status__in=[ATTEMPT_STATUS_CANCELLED, ATTEMPT_STATUS_EXPIRED],
            ).count()
            if attempt_count < override.max_attempts:
                return

    if assessment.open_at and now < assessment.open_at:
        raise ValueError(_("Assessment has not opened yet."))
    if assessment.close_at and now > assessment.close_at:
        raise ValueError(_("Assessment has already closed."))
    attempt_count = AssessmentAttempt.objects.filter(
        assessment=assessment, student=student,
    ).exclude(
        status__in=[ATTEMPT_STATUS_CANCELLED, ATTEMPT_STATUS_EXPIRED],
    ).count()
    if attempt_count >= assessment.max_attempts:
        raise ValueError(_("You have reached the maximum number of attempts."))


# ---------------------------------------------------------------------------
# Attempt lifecycle
# ---------------------------------------------------------------------------


@transaction.atomic
def start_assessment(student, assessment: Assessment) -> AssessmentAttempt:
    check_assessment_access(student, assessment)
    existing = AssessmentAttempt.objects.filter(
        assessment=assessment,
        student=student,
        status=ATTEMPT_STATUS_IN_PROGRESS,
    ).first()
    if existing:
        if _is_diploma_random(assessment):
            generate_diploma_assessment_snapshot(existing)
        return existing
    override = get_active_semester_exam_override(student, assessment)
    if override:
        attempt_number = AssessmentAttempt.objects.filter(
            assessment=assessment, student=student,
        ).exclude(
            status__in=[ATTEMPT_STATUS_CANCELLED, ATTEMPT_STATUS_EXPIRED],
        ).count() + 1
        if attempt_number > override.max_attempts:
            raise ValueError(_("You have reached the maximum number of attempts."))
    else:
        attempt_number = AssessmentAttempt.objects.filter(
            assessment=assessment, student=student,
        ).count() + 1
        if attempt_number > assessment.max_attempts:
            raise ValueError(_("You have reached the maximum number of attempts."))
    attempt = AssessmentAttempt.objects.create(
        assessment=assessment,
        student=student,
        attempt_number=attempt_number,
        status=ATTEMPT_STATUS_IN_PROGRESS,
        started_at=timezone.now(),
    )
    if override:
        attempt.is_exceptional = True
        attempt.save(update_fields=["is_exceptional"])
        consume_semester_exam_override(override, attempt)
    if _is_diploma_random(assessment):
        generate_diploma_assessment_snapshot(attempt)
    return attempt


def _is_diploma_random(assessment: Assessment) -> bool:
    """True if this is a diploma-scoped random assessment (uses snapshot questions)."""
    return assessment.course_version_id is None and bool(assessment.semester_id and assessment.cohort_id)


def generate_diploma_assessment_snapshot(attempt: AssessmentAttempt) -> None:
    """Generate per-student immutable snapshot questions for a diploma random assessment.

    Idempotent: if snapshot questions already exist, returns early.
    Raises ValueError if any section's lecture has insufficient active MCQs.
    """
    if attempt.snapshot_questions.exists():
        return

    assessment = attempt.assessment
    sections = list(assessment.lecture_sections.order_by("order").select_related("lecture"))
    if not sections:
        raise ValueError(_("Assessment has no lecture sections configured."))

    with transaction.atomic():
        attempt_ref = AssessmentAttempt.objects.select_for_update().get(pk=attempt.pk)
        if attempt_ref.snapshot_questions.exists():
            return

        order = 0

        for section in sections:
            # ponytail: manual questions copied as-is, no random selection needed
            for mq in section.manual_questions.order_by("order"):
                order += 1
                AssessmentAttemptQuestion.objects.create(
                    attempt=attempt_ref,
                    section=section,
                    source_lecture=section.lecture,
                    source_manual_question=mq,
                    source_type=SNAPSHOT_QUESTION_SOURCE_MANUAL,
                    text_snapshot=mq.text,
                    question_type=mq.question_type,
                    points=mq.points,
                    order=order,
                    required=mq.required,
                    rubric_snapshot=mq.rubric,
                )

            bank_qs = list(DiplomaLectureQuestion.objects.filter(
                lecture=section.lecture, active=True, question_type=QUESTION_TYPE_MCQ,
            ))
            if len(bank_qs) < section.mcq_count:
                raise ValueError(
                    _("Section '%(lecture)s' needs %(req)d MCQs, %(avail)d available.")
                    % {"lecture": section.lecture.title, "req": section.mcq_count, "avail": len(bank_qs)}
                )

            for q in random.sample(bank_qs, section.mcq_count):
                order += 1
                sq = AssessmentAttemptQuestion.objects.create(
                    attempt=attempt_ref,
                    section=section,
                    source_lecture=section.lecture,
                    source_bank_question=q,
                    source_type=SNAPSHOT_QUESTION_SOURCE_BANK,
                    text_snapshot=q.text,
                    question_type=q.question_type,
                    points=q.points,
                    order=order,
                    required=True,
                )
                for c in DiplomaLectureChoice.objects.filter(question=q).order_by("order"):
                    AssessmentAttemptChoice.objects.create(
                        attempt_question=sq,
                        text_snapshot=c.text,
                        is_correct_snapshot=c.is_correct,
                        order=c.order,
                    )

        _ensure_snapshot_answers(attempt_ref)


def _ensure_snapshot_answers(attempt: AssessmentAttempt) -> None:
    """Create empty snapshot answer for each question that lacks one."""
    existing = set(
        AssessmentAttemptSnapshotAnswer.objects.filter(
            attempt_question__attempt=attempt,
        ).values_list("attempt_question_id", flat=True)
    )
    to_create = []
    for sq in attempt.snapshot_questions.only("pk").all():
        if sq.pk not in existing:
            to_create.append(AssessmentAttemptSnapshotAnswer(attempt_question=sq))
    if to_create:
        AssessmentAttemptSnapshotAnswer.objects.bulk_create(to_create)


def submit_assessment(attempt: AssessmentAttempt, answers: list[AssessmentAnswerData]) -> AssessmentAttempt:
    if attempt.status != ATTEMPT_STATUS_IN_PROGRESS:
        raise ValueError(_("Assessment can only be submitted from in_progress status."))
    assessment = attempt.assessment
    now = timezone.now()
    if not assessment.is_active:
        raise ValueError(_("Assessment is not active."))
    if assessment.close_at and now > assessment.close_at and not assessment.allow_late_submission:
        raise ValueError(_("Assessment has already closed."))
    if attempt.started_at and assessment.duration_minutes:
        deadline = attempt.started_at + timezone.timedelta(minutes=assessment.duration_minutes)
        if now > deadline:
            raise ValueError(_("Assessment duration has expired."))

    questions = list(assessment.questions.select_related("assessment").prefetch_related("choices"))
    question_map = {q.id: q for q in questions}
    answered_question_ids = {answer_data.question_id for answer_data in answers}
    missing_required = [q for q in questions if q.required and q.id not in answered_question_ids]
    if missing_required:
        raise ValueError(_("Please answer all required questions before submitting."))

    has_ungraded = False
    total_score = Decimal("0.00")
    total_possible = sum(q.points for q in questions if q.required or q.id in answered_question_ids)

    with transaction.atomic():
        for answer_data in answers:
            question = question_map.get(answer_data.question_id)
            if not question:
                continue
            if question.question_type == QUESTION_TYPE_MCQ:
                if answer_data.written_text:
                    raise ValueError(_("MCQ answer must not carry written text."))
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
                    raise ValueError(_("Written answer must not carry a selected choice."))
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
        status = ATTEMPT_STATUS_UNDER_REVIEW if has_ungraded else ATTEMPT_STATUS_GRADED
        attempt.status = status
        attempt.submitted_at = now
        attempt.score_points = total_score
        if total_possible > 0:
            attempt.score_percent = (total_score / total_possible) * Decimal("100.00")
        if not has_ungraded:
            attempt.graded_at = now
            attempt.is_pass = (
                attempt.score_percent >= assessment.passing_score
                if attempt.score_percent is not None else False
            )
        attempt.save(
            update_fields=[
                "status", "submitted_at", "score_points", "score_percent",
                "graded_at", "is_pass",
            ],
        )
    return attempt


def submit_diploma_assessment(attempt: AssessmentAttempt, answer_data: dict) -> AssessmentAttempt:
    """Submit a diploma random assessment attempt.

    answer_data maps ``q_{order}`` -> selected_choice_id (MCQ) or written_text (written).
    """
    if attempt.status != ATTEMPT_STATUS_IN_PROGRESS:
        raise ValueError(_("Attempt is not in progress."))

    assessment = attempt.assessment
    now = timezone.now()
    if assessment.close_at and now > assessment.close_at:
        raise ValueError(_("Assessment has already closed."))
    if attempt.started_at and assessment.duration_minutes:
        deadline = attempt.started_at + timezone.timedelta(minutes=assessment.duration_minutes)
        if now > deadline:
            raise ValueError(_("Assessment duration has expired."))

    with transaction.atomic():
        attempt_ref = AssessmentAttempt.objects.select_for_update().get(pk=attempt.pk)

        has_written = False
        total_earned = Decimal("0")
        total_possible = Decimal("0")

        for sq in attempt_ref.snapshot_questions.order_by("order"):
            answer, created = AssessmentAttemptSnapshotAnswer.objects.get_or_create(
                attempt_question=sq,
            )
            order_key = f"q_{sq.order}"

            if sq.question_type == QUESTION_TYPE_MCQ:
                raw = answer_data.get(order_key, "")
                if raw:
                    try:
                        choice = sq.choices.get(pk=int(raw))
                    except (ValueError, AssessmentAttemptChoice.DoesNotExist):
                        raise ValueError(_("Invalid choice for question %(order)d.") % {"order": sq.order}) from None
                    answer.selected_choice = choice
                    answer.score = sq.points if choice.is_correct_snapshot else Decimal("0")
                    if not choice.is_correct_snapshot and assessment.penalty_percent:
                        answer.score = sq.points - (sq.points * assessment.penalty_percent / Decimal("100"))
                    total_earned += answer.score
                else:
                    answer.selected_choice = None
                    answer.score = Decimal("0")
                total_possible += sq.points

            elif sq.question_type == QUESTION_TYPE_WRITTEN:
                text = answer_data.get(order_key, "")
                answer.written_text = text
                answer.score = None
                if sq.required and not text.strip():
                    raise ValueError(
                        _("Question %(order)d requires an answer.") % {"order": sq.order}
                    )
                if text.strip():
                    has_written = True
                if sq.required or text.strip():
                    total_possible += sq.points

            answer.save()

        attempt_ref.score_points = total_earned
        if total_possible > 0:
            attempt_ref.score_percent = (total_earned / total_possible * Decimal("100")).quantize(Decimal("0.01"))
        attempt_ref.is_pass = (
            attempt_ref.score_percent >= assessment.passing_score
            if attempt_ref.score_percent is not None else False
        )
        if has_written:
            attempt_ref.status = ATTEMPT_STATUS_UNDER_REVIEW
        else:
            attempt_ref.status = ATTEMPT_STATUS_GRADED
        attempt_ref.submitted_at = now
        attempt_ref.save()

    return attempt_ref


def diploma_autosave_attempt(attempt: AssessmentAttempt, answer_data: dict) -> None:
    """Save in-progress snapshot answers without scoring or status change."""
    if attempt.status != ATTEMPT_STATUS_IN_PROGRESS:
        return
    for sq in attempt.snapshot_questions.order_by("order"):
        order_key = f"q_{sq.order}"
        if order_key not in answer_data:
            continue
        answer, _ = AssessmentAttemptSnapshotAnswer.objects.get_or_create(
            attempt_question=sq,
        )
        raw = answer_data[order_key]
        if sq.question_type == QUESTION_TYPE_MCQ:
            try:
                answer.selected_choice_id = int(raw) if raw else None
            except (ValueError, TypeError):
                answer.selected_choice_id = None
        elif sq.question_type == QUESTION_TYPE_WRITTEN:
            answer.written_text = raw
        answer.save(update_fields=["selected_choice", "written_text"])


# ponytail: V154 — save in-progress answers without scoring or status change.
def autosave_attempt(attempt: AssessmentAttempt, answers: list[AssessmentAnswerData]) -> None:
    if attempt.status != ATTEMPT_STATUS_IN_PROGRESS:
        return
    answers_by_question = {a.question_id: a for a in answers}
    questions = list(attempt.assessment.questions.prefetch_related("choices"))
    with transaction.atomic():
        for question in questions:
            answer_data = answers_by_question.get(question.id)
            if not answer_data:
                continue
            if question.question_type == QUESTION_TYPE_MCQ:
                AssessmentAnswer.objects.update_or_create(
                    attempt=attempt,
                    question=question,
                    defaults={
                        "selected_choice_id": answer_data.selected_choice_id,
                        "written_text": "",
                    },
                )
            elif question.question_type == QUESTION_TYPE_WRITTEN:
                AssessmentAnswer.objects.update_or_create(
                    attempt=attempt,
                    question=question,
                    defaults={
                        "selected_choice": None,
                        "written_text": answer_data.written_text,
                    },
                )


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
        raise ValueError(_("This assessment does not accept homework uploads."))
    now = timezone.now()
    if not assessment.is_active:
        raise ValueError(_("Assessment is not active."))
    if assessment.open_at and now < assessment.open_at:
        raise ValueError(_("Assessment has not opened yet."))
    if assessment.close_at and now > assessment.close_at and not assessment.allow_late_submission:
        raise ValueError(_("Assessment has already closed."))
    if not has_active_enrollment(student, assessment):
        raise ValueError(_("You do not have an active enrollment for this course."))
    if HomeworkSubmission.objects.filter(assessment=assessment, student=student).count() >= assessment.max_upload_files:
        raise ValueError(_("Maximum number of files reached for this assessment."))
    attempt_count = AssessmentAttempt.objects.filter(
        assessment=assessment, student=student,
    ).exclude(
        status__in=[ATTEMPT_STATUS_CANCELLED, ATTEMPT_STATUS_EXPIRED],
    ).count()
    if attempt_count >= assessment.max_attempts:
        raise ValueError(_("You have reached the maximum number of attempts."))
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
        raise ValueError(_("Score cannot be negative."))
    if answer.question and score > answer.question.points:
        raise ValueError(
            _("Score ({score}) cannot exceed question points ({max_points}).").format(
                score=score, max_points=answer.question.points,
            )
        )
    is_override = answer.score is not None and answer.score != score
    answer.score = score
    answer.feedback = feedback
    answer.graded_at = timezone.now()
    answer.graded_by = grader
    if is_override:
        if not override_reason:
            raise ValueError(_("An override reason is required when changing a previously-graded answer."))
        answer.score_override_reason = override_reason
    answer.save(update_fields=["score", "feedback", "graded_at", "graded_by", "score_override_reason"])
    _update_attempt_grade(answer.attempt)
    return answer


def grade_diploma_snapshot_answer(
    answer_id: int,
    score: Decimal,
    feedback: str,
    grader,
    *,
    override_reason: str = "",
) -> AssessmentAttemptSnapshotAnswer:
    answer = AssessmentAttemptSnapshotAnswer.objects.select_related(
        "attempt_question__attempt__assessment",
    ).get(pk=answer_id)

    if score < 0:
        raise ValueError(_("Score cannot be negative."))
    sq = answer.attempt_question
    if score > sq.points:
        raise ValueError(
            _("Score (%(score)s) cannot exceed question points (%(max_points)s).")
            % {"score": score, "max_points": sq.points}
        )
    is_override = answer.score is not None and answer.score != score
    if is_override and not override_reason:
        raise ValueError(_("An override reason is required when changing a previously-graded answer."))

    answer.score = score
    answer.feedback = feedback
    answer.graded_at = timezone.now()
    answer.graded_by = grader
    if is_override:
        answer.score_override_reason = override_reason
    answer.save(update_fields=["score", "feedback", "graded_at", "graded_by", "score_override_reason"])
    _update_diploma_attempt_grade(answer.attempt_question.attempt)
    return answer


def _update_diploma_attempt_grade(attempt: AssessmentAttempt) -> None:
    total = Decimal("0")
    all_graded = True
    for sq in attempt.snapshot_questions.all():
        answer = AssessmentAttemptSnapshotAnswer.objects.filter(attempt_question=sq).first()
        if answer and answer.score is not None:
            total += answer.score
        else:
            all_graded = False
    attempt.score_points = total
    total_possible = sum(sq.points for sq in attempt.snapshot_questions.all())
    assessment = attempt.assessment
    if total_possible > 0:
        attempt.score_percent = (total / total_possible * Decimal("100")).quantize(Decimal("0.01"))
    if all_graded:
        attempt.status = ATTEMPT_STATUS_GRADED
        attempt.graded_at = timezone.now()
        attempt.is_pass = (
            attempt.score_percent >= assessment.passing_score
            if attempt.score_percent is not None else None
        )
        attempt.save(update_fields=["score_points", "score_percent", "status", "graded_at", "is_pass"])
    else:
        attempt.save(update_fields=["score_points", "score_percent"])


def grade_homework(submission_id: int, score: Decimal, feedback: str, grader) -> HomeworkSubmission:
    with transaction.atomic():
        submission = HomeworkSubmission.objects.select_for_update().select_related("assessment").get(id=submission_id)
        score = validate_score(score, max_score=submission.assessment.total_points)
        if not submission.attempt_id:
            raise ValueError(_("This homework submission has no linked assessment attempt."))
        attempt = AssessmentAttempt.objects.select_for_update().get(pk=submission.attempt_id)
        now = timezone.now()
        submission.score = score
        submission.feedback = feedback
        submission.reviewed_at = now
        submission.reviewed_by = grader
        submission.status = HOMEWORK_STATUS_GRADED
        submission.save(update_fields=["score", "feedback", "reviewed_at", "reviewed_by", "status"])

        if submission.assessment.total_points <= 0:
            raise ValueError(_("Assessment total points must be positive."))
        attempt.score_points = score
        attempt.score_percent = (score / submission.assessment.total_points * Decimal("100")).quantize(Decimal("0.01"))
        attempt.graded_at = now
        attempt.graded_by = grader
        attempt.is_pass = attempt.score_percent >= submission.assessment.passing_score
        attempt.status = ATTEMPT_STATUS_GRADED
        attempt.save(update_fields=[
            "score_points", "score_percent", "graded_at", "graded_by", "is_pass", "status",
        ])
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
        raise ValueError(_("Only graded attempts can be released."))
    attempt.status = ATTEMPT_STATUS_RELEASED
    attempt.visible_to_student = True
    attempt.save(update_fields=["status", "visible_to_student"])
    return attempt


def hide_grades(attempt: AssessmentAttempt) -> AssessmentAttempt:
    attempt.visible_to_student = False
    attempt.status = ATTEMPT_STATUS_GRADED
    attempt.save(update_fields=["visible_to_student", "status"])
    return attempt


def release_all_graded(assessment: Assessment) -> int:
    """Release all graded attempts for an assessment. Returns count released."""
    attempts = AssessmentAttempt.objects.filter(
        assessment=assessment, status=ATTEMPT_STATUS_GRADED,
    )
    count = attempts.count()
    attempts.update(
        status=ATTEMPT_STATUS_RELEASED, visible_to_student=True,
    )
    return count


def hide_all_released(assessment: Assessment) -> int:
    """Hide all released attempts for an assessment. Returns count hidden."""
    attempts = AssessmentAttempt.objects.filter(
        assessment=assessment, status=ATTEMPT_STATUS_RELEASED,
    )
    count = attempts.count()
    attempts.update(
        status=ATTEMPT_STATUS_GRADED, visible_to_student=False,
    )
    return count


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def get_student_assessments(student):
    """Return assessments visible to the student (enrolled + open or active override)."""
    now = timezone.now()
    enrolled_cohort_ids = DiplomaEnrollment.objects.filter(
        student__user=student, status=ENROLLMENT_STATUS_ACTIVE,
    ).values_list("cohort_id", flat=True)
    base_qs = Assessment.objects.filter(is_active=True).filter(
        Q(course_version__enrollments__student__user=student, course_version__enrollments__status=ENROLLMENT_STATUS_ACTIVE)
        | Q(cohort_id__in=list(enrolled_cohort_ids), course_version__isnull=True)
    )
    open_assessments = list(
        base_qs.filter(
            Q(open_at__isnull=True) | Q(open_at__lte=now),
            Q(close_at__isnull=True) | Q(close_at__gte=now),
        )
        .select_related("course_version__course")
        .prefetch_related("attempts")
        .distinct()
    )
    # Filter out diploma assessments whose semester isn't visible yet
    diploma_assessments = [a for a in open_assessments if a.cohort_id and a.semester_id]
    if diploma_assessments:
        enrollments = {
            e.cohort_id: e
            for e in DiplomaEnrollment.objects.filter(
                student__user=student, status=ENROLLMENT_STATUS_ACTIVE,
                cohort_id__in={a.cohort_id for a in diploma_assessments},
            ).select_related("cohort__diploma")
        }
        visible_by_enrollment = get_visible_semester_ids_by_enrollment(enrollments.values())
        visible_by_cohort = {
            cohort_id: visible_by_enrollment.get(enrollment.id, set())
            for cohort_id, enrollment in enrollments.items()
        }
        open_assessments = [
            a for a in open_assessments
            if not (a.cohort_id and a.semester_id)
            or (a.cohort_id in visible_by_cohort and a.semester_id in visible_by_cohort[a.cohort_id])
        ]
    override_ids = SemesterExamAccessOverride.objects.filter(
        diploma_enrollment__student__user=student,
        diploma_enrollment__status=ENROLLMENT_STATUS_ACTIVE,
        status=EXAM_OVERRIDE_ACTIVE,
        open_at__lte=now,
        close_at__gte=now,
    ).values_list("assessment_id", flat=True)
    if override_ids:
        extra = list(
            Assessment.objects.filter(id__in=override_ids)
            .select_related("course_version__course")
            .prefetch_related("attempts")
        )
        existing_ids = {a.id for a in open_assessments}
        for a in extra:
            if a.id not in existing_ids:
                open_assessments.append(a)
    return open_assessments


def get_student_attempts(student):
    return AssessmentAttempt.objects.filter(student=student).select_related(
        "assessment",
    ).order_by("-created_at")


def get_pending_grading(user=None):
    qs = AssessmentAttempt.objects.filter(
        status=ATTEMPT_STATUS_UNDER_REVIEW,
    ).select_related("assessment", "assessment__cohort", "student")
    if user is not None:
        qs = qs.filter(assessment_staff_scope_filter(user)).distinct()
    return qs


def get_homework_pending_grading(user=None):
    qs = HomeworkSubmission.objects.filter(
        status__in=[HOMEWORK_STATUS_UPLOADED, HOMEWORK_STATUS_UNDER_REVIEW],
    ).select_related("assessment", "assessment__cohort", "student", "attempt")
    if user is not None:
        qs = qs.filter(assessment_staff_scope_filter(user)).distinct()
    return qs


def get_grading_queue(user):
    """Return attempts pending review, scoped to user's permissions."""
    if is_admin(user):
        return get_pending_grading()
    if user.is_authenticated:
        return get_pending_grading(user)
    return AssessmentAttempt.objects.none()


# ---------------------------------------------------------------------------
# Diploma section validation
# ---------------------------------------------------------------------------


def get_available_bank_mcq_count(lecture):
    """Return count of active MCQ questions in the lecture's bank."""
    return DiplomaLectureQuestion.objects.filter(
        lecture=lecture, active=True, question_type=QUESTION_TYPE_MCQ,
    ).count()


def validate_sections_ready(assessment):
    """Raise ValueError if assessment has sections that cannot generate required MCQ count
    or if total max_grade doesn't match assessment.total_points."""
    for section in assessment.lecture_sections.all():
        available = get_available_bank_mcq_count(section.lecture)
        if section.mcq_count > available:
            raise ValueError(
                _("Section '%(lecture)s' requires %(required)d MCQs but only %(available)d available.")
                % {"lecture": section.lecture.title, "required": section.mcq_count, "available": available}
            )
    total_max = sum(section.max_grade for section in assessment.lecture_sections.all())
    if total_max != assessment.total_points:
        raise ValueError(
            _("Total section max grade (%(total)s) does not match assessment total points (%(expected)s).")
            % {"total": total_max, "expected": assessment.total_points}
        )


# ---------------------------------------------------------------------------
# Exam code generation and offline grade entry (Phase 7)
# ---------------------------------------------------------------------------


def generate_exam_codes(assessment: Assessment, actor) -> int:
    if assessment.assessment_type != ASSESSMENT_TYPE_SEMESTER_EXAM:
        raise ValueError(_("Exam codes are only for semester exams."))
    if not assessment.cohort:
        raise ValueError(_("Assessment must have a cohort."))

    enrollments = DiplomaEnrollment.objects.filter(
        cohort=assessment.cohort,
        status=ENROLLMENT_STATUS_ACTIVE,
    ).select_related("student__user")

    existing_codes = set(
        SemesterExamCode.objects.filter(assessment=assessment).values_list("diploma_enrollment_id", flat=True)
    )
    existing_numbers = set(
        SemesterExamCode.objects.filter(assessment=assessment).values_list("code_number", flat=True)
    )

    new_codes = []
    for de in enrollments:
        if de.pk in existing_codes:
            continue
        for _attempt in range(1000):
            code = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
            if code not in existing_numbers:
                existing_numbers.add(code)
                break
        else:
            raise ValueError(_("Could not generate a unique exam code after 1000 attempts."))
        new_codes.append(SemesterExamCode(
            assessment=assessment, diploma_enrollment=de,
            code_number=code, delivery_mode=DELIVERY_MODE_ONLINE,
            created_by=actor, active=True,
        ))

    if new_codes:
        SemesterExamCode.objects.bulk_create(new_codes)
    return len(new_codes)


def set_exam_code_delivery_mode(code_id: int, mode: str) -> SemesterExamCode:
    code = SemesterExamCode.objects.get(pk=code_id)
    code.delivery_mode = mode
    code.save(update_fields=["delivery_mode"])
    return code


def enter_offline_section_grade(exam_code_id: int, section_id: int, score: Decimal, grader) -> OfflineSectionGrade:
    section = AssessmentLectureSection.objects.get(pk=section_id)
    validate_score(score, max_score=section.max_grade)
    grade, created = OfflineSectionGrade.objects.update_or_create(
        exam_code_id=exam_code_id, section_id=section_id,
        defaults={"score": score, "graded_by": grader},
    )
    return grade


def finalize_offline_attempt(exam_code_id: int) -> AssessmentAttempt:
    exam_code = SemesterExamCode.objects.select_related("assessment", "diploma_enrollment__student__user").get(pk=exam_code_id)
    assessment = exam_code.assessment
    with transaction.atomic():
        attempt, _ = AssessmentAttempt.objects.get_or_create(
            assessment=assessment, student=exam_code.diploma_enrollment.student.user,
            attempt_number=1,
            defaults={"status": ATTEMPT_STATUS_GRADED, "submitted_at": timezone.now()},
        )
        total_score = Decimal("0")
        total_max = Decimal("0")
        all_graded = True
        for section in assessment.lecture_sections.all():
            total_max += section.max_grade
            grade = OfflineSectionGrade.objects.filter(exam_code=exam_code, section=section).first()
            if grade:
                total_score += grade.score
            else:
                all_graded = False
        if all_graded:
            attempt.score_points = total_score
            if total_max > 0:
                attempt.score_percent = (total_score / total_max * Decimal("100")).quantize(Decimal("0.01"))
            attempt.is_pass = attempt.score_percent >= assessment.passing_score if attempt.score_percent else False
            attempt.status = ATTEMPT_STATUS_GRADED
            attempt.submitted_at = timezone.now()
            attempt.visible_to_student = False
            attempt.save()
    return attempt


# ---------------------------------------------------------------------------
# Reviewer services (Phase 8)
# ---------------------------------------------------------------------------


def get_reviewer_ungraded_count(assignment) -> int:
    """Return count of ungraded exam codes for a reviewer assignment's section."""
    if not assignment.section_id:
        return 0
    total = SemesterExamCode.objects.filter(
        assessment__lecture_sections=assignment.section,
        active=True,
        diploma_enrollment__cohort=assignment.cohort,
    ).count()
    graded = OfflineSectionGrade.objects.filter(
        section=assignment.section,
        exam_code__assessment__lecture_sections=assignment.section,
        exam_code__diploma_enrollment__cohort=assignment.cohort,
    ).values("exam_code_id").distinct().count()
    return total - graded


def reviewer_grade_section(exam_code_id: int, section_id: int, score: Decimal, grader) -> OfflineSectionGrade:
    """Enter or update an offline section grade as a reviewer."""
    section = AssessmentLectureSection.objects.get(pk=section_id)
    validate_score(score, max_score=section.max_grade)
    grade, created = OfflineSectionGrade.objects.update_or_create(
        exam_code_id=exam_code_id, section_id=section_id,
        defaults={"score": score, "graded_by": grader},
    )
    return grade


def can_reviewer_grade_code(assignment, exam_code) -> bool:
    """Check that the exam code belongs to the reviewer's assigned cohort, semester, and section."""
    if assignment.cohort_id != exam_code.diploma_enrollment.cohort_id:
        return False
    if assignment.semester_id != exam_code.assessment.semester_id:
        return False
    if assignment.section_id and assignment.section.assessment_id != exam_code.assessment_id:
        return False
    return True


def can_reviewer_grade_section(assignment, section) -> bool:
    """Check that the section belongs to the reviewer's assignment."""
    if assignment.section_id and assignment.section_id != section.pk:
        return False
    return True


def can_take_diploma_assessment(student, assessment):
    if not has_active_enrollment(student, assessment):
        return False
    if assessment.assessment_type == ASSESSMENT_TYPE_SEMESTER_EXAM:
        code = SemesterExamCode.objects.filter(
            assessment=assessment,
            diploma_enrollment__student__user=student,
            active=True,
        ).first()
        if code and code.delivery_mode == DELIVERY_MODE_OFFLINE:
            return False
    return True


# ---------------------------------------------------------------------------
# Exceptional semester exam access overrides (Phase 9)
# ---------------------------------------------------------------------------


@transaction.atomic
def create_semester_exam_access_overrides(assessment, enrollments, *, open_at, close_at, reason, actor, force_online=False) -> list[SemesterExamAccessOverride]:
    if assessment.assessment_type != ASSESSMENT_TYPE_SEMESTER_EXAM:
        raise ValueError(_("Overrides are only allowed for semester exams."))
    if assessment.course_version_id is not None or not assessment.cohort_id or not assessment.semester_id:
        raise ValueError(_("Overrides are only allowed for diploma-scoped semester exams."))
    if open_at >= close_at:
        raise ValueError(_("open_at must be before close_at."))
    if not reason:
        raise ValueError(_("Reason is required."))

    generate_exam_codes(assessment, actor)

    created = []
    for enrollment in enrollments:
        if enrollment.cohort_id != assessment.cohort_id:
            continue
        if enrollment.status != ENROLLMENT_STATUS_ACTIVE:
            continue
        if SemesterExamAccessOverride.objects.filter(
            assessment=assessment, diploma_enrollment=enrollment,
            status=EXAM_OVERRIDE_ACTIVE,
        ).exists():
            continue

        exam_code = SemesterExamCode.objects.filter(
            assessment=assessment, diploma_enrollment=enrollment,
        ).first()

        if force_online and exam_code and exam_code.delivery_mode != DELIVERY_MODE_ONLINE:
            exam_code.delivery_mode = DELIVERY_MODE_ONLINE
            exam_code.save(update_fields=["delivery_mode"])

        override = SemesterExamAccessOverride.objects.create(
            assessment=assessment,
            diploma_enrollment=enrollment,
            exam_code=exam_code,
            open_at=open_at,
            close_at=close_at,
            max_attempts=1,
            reason=reason,
            created_by=actor,
        )
        record_audit_event(
            actor=actor,
            action=ACTION_EXAM_ACCESS_OVERRIDE_CREATED,
            target_type="SemesterExamAccessOverride",
            target_id=str(override.pk),
            metadata={
                "assessment_id": assessment.pk,
                "enrollment_id": enrollment.pk,
                "reason": reason,
            },
        )
        created.append(override)
    return created


def cancel_semester_exam_access_override(override, *, actor, reason="") -> SemesterExamAccessOverride:
    if not reason.strip():
        raise ValueError(_("Reason is required to cancel an override."))
    if override.status != EXAM_OVERRIDE_ACTIVE:
        raise ValueError(_("Override is not active."))
    override.status = EXAM_OVERRIDE_CANCELLED
    override.cancelled_by = actor
    override.cancelled_at = timezone.now()
    override.save()
    record_audit_event(
        actor=actor,
        action=ACTION_EXAM_ACCESS_OVERRIDE_CANCELLED,
        target_type="SemesterExamAccessOverride",
        target_id=str(override.pk),
        metadata={"reason": reason},
    )
    return override


def get_active_semester_exam_override(student, assessment, now=None) -> SemesterExamAccessOverride | None:
    now = now or timezone.now()
    enrollment = DiplomaEnrollment.objects.filter(
        student__user=student,
        cohort=assessment.cohort,
        status=ENROLLMENT_STATUS_ACTIVE,
    ).first()
    if not enrollment:
        return None
    return SemesterExamAccessOverride.objects.filter(
        diploma_enrollment=enrollment,
        assessment=assessment,
        status=EXAM_OVERRIDE_ACTIVE,
    ).first()


def check_semester_exam_access(student, assessment, now=None) -> bool:
    try:
        check_assessment_access(student, assessment)
        return True
    except ValueError:
        now = now or timezone.now()
        override = get_active_semester_exam_override(student, assessment, now)
        if override and override.open_at <= now <= override.close_at:
            return True
        return False


@transaction.atomic
def consume_semester_exam_override(override, attempt) -> SemesterExamAccessOverride:
    SemesterExamAccessOverride.objects.select_for_update().get(pk=override.pk)
    override.refresh_from_db()
    override.used_count += 1
    override.used_at = timezone.now()
    override.attempt = attempt
    if override.used_count >= override.max_attempts:
        override.status = EXAM_OVERRIDE_USED
    override.save()
    record_audit_event(
        actor=None,
        action=ACTION_EXAM_ACCESS_OVERRIDE_USED,
        target_type="SemesterExamAccessOverride",
        target_id=str(override.pk),
        metadata={"attempt_id": attempt.pk, "assessment_id": override.assessment_id, "used_count": override.used_count},
    )
    return override
