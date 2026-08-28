"""Entrance exam business logic: assignment, submission, scoring, and result update."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.applications.constants import (
    EMAIL_ACTION_NOTE,
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_INTERVIEW_REQUESTED,
    PROGRAM_APP_STATUS_REJECTED,
    PROGRAM_TYPE_DIPLOMA,
)
from apps.applications.services import mark_entrance_submitted, record_program_application_note
from apps.audit.services import record_audit_event
from apps.communications.constants import (
    TEMPLATE_ENTRANCE_EXAM_FAIL,
    TEMPLATE_ENTRANCE_EXAM_PASS,
)
from apps.core.permissions import validate_score
from apps.core.scoring import compute_percent, is_passing

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

logger = logging.getLogger(__name__)

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


def attempt_decision(attempt: EntranceAttempt) -> dict | None:
    """Return the admin's decision on an attempt's application, or None.

    The decision is derived from the ProgramApplication status (accepted,
    rejected, or interview requested). Grades are never exposed to students,
    so this is the only outcome shown on the results page.
    """
    status = attempt.program_application.status
    if status == PROGRAM_APP_STATUS_ACCEPTED:
        return {"code": "accepted", "label": _("Accepted"), "tone": "success"}
    if status == PROGRAM_APP_STATUS_REJECTED:
        return {"code": "rejected", "label": _("Rejected"), "tone": "danger"}
    if status == PROGRAM_APP_STATUS_INTERVIEW_REQUESTED:
        return {"code": "interview_requested", "label": _("Interview requested"), "tone": "info"}
    return None


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
    if attempt.submitted_at:
        raise ValueError(_("This exam has already been taken and cannot be started again."))
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


def attempt_deadline(attempt: EntranceAttempt):
    """Return the authoritative deadline for an attempt, or None.

    The deadline is ``started_at + duration`` when the attempt has started,
    otherwise ``now + duration`` (the countdown starts at page load).
    """
    exam = attempt.exam
    if not exam.duration_minutes:
        return None
    base = attempt.started_at or timezone.now()
    return base + timezone.timedelta(minutes=exam.duration_minutes)


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
    deadline = attempt_deadline(attempt)
    if deadline and now > deadline:
        raise ValueError(_("Exam duration has expired."))
    questions = list(exam.questions.all())
    question_map = {q.id: q for q in questions}
    # Answer coverage: every exam question must be answered exactly once,
    # otherwise the attempt is not finalized and nothing is auto-graded (E4).
    answered_ids = {answer_data.question_id for answer_data in answers}
    unknown_ids = answered_ids - set(question_map)
    if unknown_ids:
        raise ValueError(_("Unknown question in submission."))
    if len(answers) != len(answered_ids):
        raise ValueError(_("Each exam question can only be answered once."))
    missing_ids = [q.id for q in questions if q.id not in answered_ids]
    if missing_ids:
        raise ValueError(_("Please answer all exam questions before submitting."))
    with transaction.atomic():
        # Lock the attempt row so a concurrent submit/finalize is serialized
        # instead of racing into a duplicate-answer IntegrityError.
        locked = EntranceAttempt.objects.select_for_update().get(pk=attempt.pk)
        if locked.status != ATTEMPT_STATUS_IN_PROGRESS:
            raise ValueError(_("Exam can only be submitted from in_progress status."))
        now = timezone.now()
        deadline = attempt_deadline(locked)
        if deadline and now > deadline:
            raise ValueError(_("Exam duration has expired."))
        for answer_data in answers:
            question = question_map[answer_data.question_id]
            if question.question_type == QUESTION_TYPE_MCQ:
                if answer_data.written_text:
                    raise ValueError(_("MCQ answer must not carry written text."))
                choice = EntranceChoice.objects.filter(
                    id=answer_data.selected_choice_id, question=question,
                ).first()
                score = choice.score if choice else Decimal("0.00")
                EntranceAnswer.objects.update_or_create(
                    attempt=locked,
                    question=question,
                    defaults={"selected_choice": choice, "score": score},
                )
            else:
                if answer_data.selected_choice_id is not None:
                    raise ValueError(_("Written answer must not carry a selected choice."))
                EntranceAnswer.objects.update_or_create(
                    attempt=locked,
                    question=question,
                    defaults={"written_text": answer_data.written_text},
                )
        locked.status = ATTEMPT_STATUS_COMPLETED
        locked.submitted_at = timezone.now()
        locked.save(update_fields=["status", "submitted_at"])
        _recompute_attempt_grade(locked)
    return locked


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


def grade_answer(
    answer: EntranceAnswer,
    score: Decimal,
    feedback: str,
    grader: User,
) -> EntranceAnswer:
    validate_score(score)
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
        percentage = compute_percent(total, exam.total_score)
        # entrance exams always have a total_score; fallback to 0% when none
        if percentage is None:
            percentage = Decimal("0.00")
        is_pass = is_passing(percentage, exam.pass_percentage)
        attempt.result = RESULT_PASS if is_pass else RESULT_FAIL
        attempt.status = ATTEMPT_STATUS_GRADED
        attempt.graded_at = timezone.now()
        attempt.save(update_fields=["score", "status", "graded_at", "result"])
        _update_program_application_from_result(attempt, attempt.result, actor=actor)
    else:
        attempt.save(update_fields=["score"])


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

    if prog_app.status in ("invited_to_entrance", "submitted"):
        mark_entrance_submitted(prog_app, actor=actor, attempt=attempt)
    note_text = _("Exam passed") if result == RESULT_PASS else _("Exam failed")
    record_program_application_note(prog_app, actor, note_text)

    if not attempt.exam.auto_send_results:
        return

    # The result email is an external effect: schedule it after commit so an
    # SMTP failure never rolls back the already-durable submission (BUG-004).
    transaction.on_commit(
        lambda: _send_result_email(attempt, result, actor),
    )


def _send_result_email(
    attempt: EntranceAttempt,
    result: str,
    actor: User | None = None,
) -> None:
    """Send the entrance result email after the submission commit.

    The email is best-effort: a send or log failure is logged and never
    raises into the caller (the submission has already committed). Uses the
    canonical queue pipeline so recipient, action, template, status, timestamps,
    actor and idempotency are handled centrally and Celery updates the log.
    """
    try:
        from apps.communications.services import queue_program_application_email

        prog_app = attempt.program_application
        slug = TEMPLATE_ENTRANCE_EXAM_PASS if result == RESULT_PASS else TEMPLATE_ENTRANCE_EXAM_FAIL
        email_to = attempt.student.user.email
        context = {
            "full_name": attempt.student.full_name or "",
            "exam_title": attempt.exam.title,
            "cohort_name": attempt.exam.cohort.name or "",
        }
        queue_program_application_email(
            program_application=prog_app,
            action=EMAIL_ACTION_NOTE,
            template_slug=slug,
            to_email=email_to,
            context=context,
            sent_by=actor,
            idempotency_key=f"entrance_exam:result:attempt:{attempt.pk}",
            reference=f"attempt:{attempt.pk}",
        )
    except Exception:
        logger.exception("Entrance result email failed for attempt %s", attempt.id)


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
    attempt, _created = EntranceAttempt.objects.get_or_create(
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


def get_pending_grading() -> list[EntranceAttempt]:
    return list(
        EntranceAttempt.objects.filter(status=ATTEMPT_STATUS_COMPLETED)
        .select_related("exam", "student__user")
        .order_by("submitted_at")
    )


# ---------------------------------------------------------------------------
# Snapshot save & finalization
# ---------------------------------------------------------------------------


def save_snapshot(attempt: EntranceAttempt, question_id: int, choice_id: int | None = None, written_text: str = "") -> None:
    """Save or update a single answer snapshot for the given attempt/question."""
    if attempt.status != ATTEMPT_STATUS_IN_PROGRESS:
        raise ValueError(_("Exam can only be saved from in_progress status."))
    exam = attempt.exam
    now = timezone.now()
    if not exam.is_active:
        raise ValueError(_("Exam is not active."))
    if exam.open_at and now < exam.open_at:
        raise ValueError(_("Exam has not opened yet."))
    if exam.close_at and now > exam.close_at:
        raise ValueError(_("Exam has already closed."))
    deadline = attempt_deadline(attempt)
    if deadline and now > deadline:
        raise ValueError(_("Exam duration has expired."))
    question = attempt.exam.questions.filter(id=question_id).first()
    if not question:
        return
    with transaction.atomic():
        locked = EntranceAttempt.objects.select_for_update().get(pk=attempt.pk)
        if locked.status != ATTEMPT_STATUS_IN_PROGRESS:
            raise ValueError(_("Exam can only be saved from in_progress status."))
        now = timezone.now()
        deadline = attempt_deadline(locked)
        if deadline and now > deadline:
            raise ValueError(_("Exam duration has expired."))
        EntranceSnapshot.objects.update_or_create(
            attempt=locked,
            question=question,
            defaults={
                "selected_choice_id": choice_id,
                "written_text": written_text,
            },
        )


def finalize_attempt_from_snapshots(attempt: EntranceAttempt) -> EntranceAttempt:
    """Rebuild answers from the latest snapshots and submit the attempt.

    Used when a student's browser fails to submit (timeout, crash, expiry)
    or an admin triggers recovery. Bypasses deadline checks. Idempotent:
    a terminal attempt is returned unchanged.
    """
    with transaction.atomic():
        locked = EntranceAttempt.objects.select_for_update().get(pk=attempt.pk)
        if locked.status != ATTEMPT_STATUS_IN_PROGRESS:
            return locked
        snapshots = EntranceSnapshot.objects.filter(attempt=locked).select_related("question")
        if not snapshots:
            locked.status = ATTEMPT_STATUS_COMPLETED
            locked.submitted_at = timezone.now()
            locked.save(update_fields=["status", "submitted_at"])
            return locked
        questions = {q.id: q for q in locked.exam.questions.all()}
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
                    attempt=locked,
                    question=question,
                    defaults={
                        "selected_choice": choice,
                        "score": score,
                    },
                )
            else:
                EntranceAnswer.objects.update_or_create(
                    attempt=locked,
                    question=question,
                    defaults={
                        "written_text": snap.written_text,
                    },
                )
        locked.status = ATTEMPT_STATUS_COMPLETED
        locked.submitted_at = timezone.now()
        locked.save(update_fields=["status", "submitted_at"])
        _recompute_attempt_grade(locked)
    return locked


def finalize_expired_attempt(attempt: EntranceAttempt) -> EntranceAttempt:
    """Idempotently finalize an attempt once its deadline has passed.

    Reachable by the client at countdown zero. Only snapshots that reached
    the backend before the deadline are used — localStorage is never
    authoritative after expiry. Safe to call repeatedly: a terminal attempt
    is returned unchanged with its current result/state.
    """
    with transaction.atomic():
        locked = EntranceAttempt.objects.select_for_update().get(pk=attempt.pk)
        if locked.status in (ATTEMPT_STATUS_COMPLETED, ATTEMPT_STATUS_GRADED):
            return locked
        if locked.status != ATTEMPT_STATUS_IN_PROGRESS:
            raise ValueError(_("Exam can only be finalized from in_progress status."))
        deadline = attempt_deadline(locked)
        if deadline is None or timezone.now() <= deadline:
            raise ValueError(_("Exam duration has not expired yet."))
        return finalize_attempt_from_snapshots(locked)


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
