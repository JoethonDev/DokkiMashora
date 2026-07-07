"""Entrance exam business logic: assignment, submission, scoring, and result update."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.applications.constants import (
    EMAIL_ACTION_NOTE,
    PROGRAM_TYPE_DIPLOMA,
)
from apps.applications.models import ProgramApplicationEmailLog as PAEL  # noqa: N817
from apps.applications.services import mark_entrance_submitted, record_program_application_note
from apps.audit.services import record_audit_event
from apps.communications.constants import (
    TEMPLATE_ENTRANCE_EXAM_FAIL,
    TEMPLATE_ENTRANCE_EXAM_PASS,
)
from apps.communications.services import send_templated_email
from apps.core.permissions import entrance_staff_scope_filter

from .constants import (
    ATTEMPT_STATUS_ASSIGNED,
    ATTEMPT_STATUS_COMPLETED,
    ATTEMPT_STATUS_GRADED,
    ATTEMPT_STATUS_IN_PROGRESS,
    QUESTION_TYPE_MCQ,
    RESULT_FAIL,
    RESULT_PASS,
)
from .models import EntranceAnswer, EntranceAttempt, EntranceChoice, EntranceExam, EntranceSnapshot

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from apps.applications.models import ProgramApplication
    from apps.students.models import StudentProfile

    from .models import EntranceAnswer, EntranceAttempt, EntranceExam


@dataclass
class AnswerData:
    question_id: int
    selected_choice_id: int | None = None
    written_text: str = ""


# ---------------------------------------------------------------------------
# Assignment / attempt lifecycle
# ---------------------------------------------------------------------------


def assign_attempt(
    exam: EntranceExam,
    program_application: ProgramApplication,
    student: StudentProfile,
) -> EntranceAttempt:
    return EntranceAttempt.objects.create(
        exam=exam,
        program_application=program_application,
        student=student,
    )


def start_attempt(attempt: EntranceAttempt) -> EntranceAttempt:
    if attempt.status != ATTEMPT_STATUS_ASSIGNED:
        raise ValueError(_("Attempt can only be started from assigned status."))
    exam = attempt.exam
    now = timezone.now()
    if not exam.is_active:
        raise ValueError(_("Exam is not active."))
    if exam.open_at and now < exam.open_at:
        raise ValueError(_("Exam has not opened yet."))
    if exam.close_at and now > exam.close_at:
        raise ValueError(_("Exam has already closed."))
    attempt.status = ATTEMPT_STATUS_IN_PROGRESS
    attempt.started_at = now
    attempt.save(update_fields=["status", "started_at"])
    return attempt


def submit_attempt(
    attempt: EntranceAttempt,
    answers: list[AnswerData],
) -> EntranceAttempt:
    if attempt.status != ATTEMPT_STATUS_IN_PROGRESS:
        raise ValueError(_("Exam can only be submitted from in_progress status."))
    exam = attempt.exam
    now = timezone.now()
    if not exam.is_active:
        raise ValueError(_("Exam is not active."))
    if exam.close_at and now > exam.close_at:
        raise ValueError(_("Exam has already closed."))
    if attempt.started_at and exam.duration_minutes:
        deadline = attempt.started_at + timezone.timedelta(minutes=exam.duration_minutes)
        if now > deadline:
            raise ValueError(_("Exam duration has expired."))
    questions = list(exam.questions.all())
    question_map = {q.id: q for q in questions}
    with transaction.atomic():
        for answer_data in answers:
            question = question_map[answer_data.question_id]
            if question.question_type == QUESTION_TYPE_MCQ:
                if answer_data.written_text:
                    raise ValueError(_("MCQ answer must not carry written text."))
                choice = EntranceChoice.objects.filter(
                    id=answer_data.selected_choice_id, question=question,
                ).first()
                score = choice.score if choice else Decimal("0.00")
                EntranceAnswer.objects.create(
                    attempt=attempt,
                    question=question,
                    selected_choice=choice,
                    score=score,
                )
            else:
                if answer_data.selected_choice_id is not None:
                    raise ValueError(_("Written answer must not carry a selected choice."))
                EntranceAnswer.objects.create(
                    attempt=attempt,
                    question=question,
                    written_text=answer_data.written_text,
                )
        attempt.status = ATTEMPT_STATUS_COMPLETED
        attempt.submitted_at = timezone.now()
        attempt.save(update_fields=["status", "submitted_at"])
        _recompute_attempt_grade(attempt)
    return attempt


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


def grade_answer(
    answer: EntranceAnswer,
    score: Decimal,
    feedback: str,
    grader: User,
) -> EntranceAnswer:
    answer.score = score
    answer.feedback = feedback
    answer.graded_at = timezone.now()
    answer.graded_by = grader
    answer.save(update_fields=["score", "feedback", "graded_at", "graded_by"])
    _recompute_attempt_grade(answer.attempt, actor=grader)
    return answer


def _recompute_attempt_grade(attempt: EntranceAttempt, actor: User | None = None) -> None:
    total = Decimal("0.00")
    all_graded = True
    for answer in attempt.answers.select_related("question").all():
        if answer.score is not None:
            total += answer.score
        else:
            all_graded = False
    attempt.score = total
    if all_graded:
        exam = attempt.exam
        percentage = (total / exam.total_score * 100) if exam.total_score else Decimal("0.00")
        attempt.result = RESULT_PASS if percentage >= exam.pass_percentage else RESULT_FAIL
        attempt.status = ATTEMPT_STATUS_GRADED
        attempt.graded_at = timezone.now()
        attempt.save(update_fields=["score", "status", "graded_at", "result"])
        _update_program_application_from_result(attempt, attempt.result, actor=actor)
    else:
        attempt.save(update_fields=["score"])


def _compute_result(score: Decimal, total_score: Decimal, pass_percentage: Decimal) -> str:
    percentage = (score / total_score * 100) if total_score else Decimal("0.00")
    return RESULT_PASS if percentage >= pass_percentage else RESULT_FAIL


def _update_program_application_from_result(
    attempt: EntranceAttempt,
    result: str,
    actor: User | None = None,
) -> None:
    prog_app = attempt.program_application

    record_audit_event(
        actor=actor,
        action="exam_completed",
        target_type="entrance_attempt",
        target_id=str(attempt.id),
        metadata={
            "student_id": attempt.student_id,
            "exam_id": attempt.exam_id,
            "score": str(attempt.score),
            "result": result,
        },
    )

    mark_entrance_submitted(prog_app, actor=actor, attempt=attempt)
    note_text = _("Exam passed") if result == RESULT_PASS else _("Exam failed")
    record_program_application_note(prog_app, actor, note_text)

    if not attempt.exam.auto_send_results:
        return

    slug = TEMPLATE_ENTRANCE_EXAM_PASS if result == RESULT_PASS else TEMPLATE_ENTRANCE_EXAM_FAIL
    email_to = attempt.student.user.email
    context = {
        "full_name": attempt.student.full_name or "",
        "exam_title": attempt.exam.title,
        "cohort_name": attempt.exam.cohort.name or "",
    }
    record = send_templated_email(slug=slug, to_email=email_to, context=context, sent_by=actor)
    if record:
        PAEL.objects.create(
            program_application=prog_app,
            action=EMAIL_ACTION_NOTE,
            template_slug=slug,
            recipient=email_to,
            sent_by=actor,
            sent_at=record.sent_at or timezone.now(),
            status=record.status or "sent",
        )


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def assign_exam_for_application(program_application: ProgramApplication) -> EntranceAttempt | None:
    """Auto-assign the matching entrance exam when a ProgramApplication is invited.

    Looks up the active EntranceExam for the application's cohort and creates
    an EntranceAttempt.  Returns ``None`` when no exam exists (the admin can
    still create one later and assign manually).
    """
    if program_application.program_type != PROGRAM_TYPE_DIPLOMA:
        return None
    cohort = program_application.cohort
    if not cohort:
        return None
    exam = EntranceExam.objects.filter(cohort=cohort, is_active=True).first()
    if not exam:
        return None
    attempt, _ = EntranceAttempt.objects.get_or_create(
        exam=exam,
        program_application=program_application,
        student=program_application.student,
    )
    return attempt


def get_student_attempts(student: StudentProfile) -> list[EntranceAttempt]:
    return list(
        EntranceAttempt.objects.filter(student=student)
        .select_related("exam", "program_application")
        .order_by("-assigned_at")
    )


def get_pending_grading(user=None) -> list[EntranceAttempt]:
    qs = (
        EntranceAttempt.objects.filter(status=ATTEMPT_STATUS_COMPLETED)
        .select_related("exam", "student__user")
        .order_by("submitted_at")
    )
    if user is not None:
        qs = qs.filter(entrance_staff_scope_filter(user)).distinct()
    return list(qs)


def get_active_exams_for_cohort(cohort_id: int) -> list[EntranceExam]:
    now = timezone.now()
    return list(
        EntranceExam.objects.filter(
            cohort_id=cohort_id,
            is_active=True,
            open_at__lte=now,
            close_at__gte=now,
        )
    )


def student_has_attempt(student: StudentProfile, exam: EntranceExam) -> EntranceAttempt | None:
    return EntranceAttempt.objects.filter(
        student=student,
        exam=exam,
    ).exclude(
        status=ATTEMPT_STATUS_ASSIGNED,
    ).first()


# ---------------------------------------------------------------------------
# Snapshot save & finalization
# ---------------------------------------------------------------------------


def save_snapshot(attempt: EntranceAttempt, question_id: int, choice_id: int | None = None, written_text: str = "") -> None:
    """Save or update a single answer snapshot for the given attempt/question."""
    question = attempt.exam.questions.filter(id=question_id).first()
    if not question:
        return
    snapshot, _ = EntranceSnapshot.objects.update_or_create(
        attempt=attempt,
        question=question,
        defaults={
            "selected_choice_id": choice_id,
            "written_text": written_text,
        },
    )


def finalize_attempt_from_snapshots(attempt: EntranceAttempt) -> EntranceAttempt:
    """Rebuild answers from the latest snapshots and submit the attempt.

    Used when a student's browser fails to submit (timeout, crash, etc.)
    and an admin triggers recovery. Bypasses deadline checks.
    """
    if attempt.status != ATTEMPT_STATUS_IN_PROGRESS:
        return attempt
    snapshots = EntranceSnapshot.objects.filter(attempt=attempt).select_related("question")
    if not snapshots:
        attempt.status = ATTEMPT_STATUS_COMPLETED
        attempt.submitted_at = timezone.now()
        attempt.save(update_fields=["status", "submitted_at"])
        return attempt
    questions = {q.id: q for q in attempt.exam.questions.all()}
    with transaction.atomic():
        for snap in snapshots:
            question = questions.get(snap.question_id)
            if not question:
                continue
            if question.question_type == QUESTION_TYPE_MCQ:
                choice = EntranceChoice.objects.filter(
                    id=snap.selected_choice_id, question=question,
                ).first() if snap.selected_choice_id else None
                score = choice.score if choice else Decimal("0.00")
                EntranceAnswer.objects.update_or_create(
                    attempt=attempt,
                    question=question,
                    defaults={
                        "selected_choice": choice,
                        "score": score,
                    },
                )
            else:
                EntranceAnswer.objects.update_or_create(
                    attempt=attempt,
                    question=question,
                    defaults={
                        "written_text": snap.written_text,
                    },
                )
        attempt.status = ATTEMPT_STATUS_COMPLETED
        attempt.submitted_at = timezone.now()
        attempt.save(update_fields=["status", "submitted_at"])
        _recompute_attempt_grade(attempt)
    return attempt


def finalize_all_pending_for_exam(exam: EntranceExam) -> int:
    """Finalize all in-progress attempts for an exam using their snapshots.

    Returns the count of finalized attempts.
    """
    attempts = EntranceAttempt.objects.filter(
        exam=exam, status=ATTEMPT_STATUS_IN_PROGRESS,
    )
    count = 0
    for attempt in attempts:
        finalize_attempt_from_snapshots(attempt)
        count += 1
    return count
