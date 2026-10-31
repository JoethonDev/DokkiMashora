"""Entrance exam business logic: assignment, submission, scoring, and result update."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.applications.constants import (
    EMAIL_ACTION_ENTRANCE_EXAM_INVITATION,
    EMAIL_ACTION_EXAM_RESULT,
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED,
    PROGRAM_APP_STATUS_INTERVIEW_REQUESTED,
    PROGRAM_APP_STATUS_INVITED,
    PROGRAM_APP_STATUS_REJECTED,
    PROGRAM_TYPE_DIPLOMA,
)
from apps.applications.models import ProgramApplication, ProgramApplicationEmailLog
from apps.applications.services import mark_entrance_submitted, record_program_application_note
from apps.audit.constants import ACTION_ENTRANCE_EXAM_COMPLETED, SECTION_ADMISSIONS
from apps.audit.services import record_audit_event
from apps.communications.constants import (
    TEMPLATE_ENTRANCE_EXAM_FAIL,
    TEMPLATE_ENTRANCE_EXAM_INVITATION,
    TEMPLATE_ENTRANCE_EXAM_PASS,
)
from apps.communications.services import queue_program_application_email
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

    from apps.students.models import StudentProfile


@dataclass
class AnswerData:
    question_id: int
    selected_choice_id: int | None = None
    written_text: str = ""


# A shared exam UUID is only a lookup key.  It is not an assignment and it
# must not create one.  These are the only application states in which the
# issued exam link remains valid; accepted/rejected/activated applications
# use the student portal's application projection instead.
PUBLIC_TOKEN_APPLICATION_STATUSES = frozenset({
    PROGRAM_APP_STATUS_INVITED,
    PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED,
})
TERMINAL_ATTEMPT_STATUSES = frozenset({
    ATTEMPT_STATUS_COMPLETED,
    ATTEMPT_STATUS_GRADED,
})
ENTRANCE_RESULT_EMAIL_SUBJECTS = {
    "ar": "تم تقديم امتحان القبول",
    "en": "Your entrance exam was submitted",
}


def entrance_attempt_audit_fields(attempt: EntranceAttempt) -> dict[str, str]:
    """Snapshot audit target/subject labels for one entrance attempt.

    Uses the loaded ``exam``/``student``/``program_application`` relations when
    the caller selected them; the values are stable identifiers, not secrets.
    """
    application = attempt.program_application
    student = attempt.student
    program_label = ""
    if application.cohort_id:
        cohort = application.cohort
        if cohort is not None:
            diploma = cohort.diploma if cohort.diploma_id else None
            program_label = " · ".join(
                part for part in ((diploma.title if diploma is not None else ""), cohort.name) if part
            )
    elif application.course_id:
        course = application.course
        program_label = (course.title if course is not None else "") or ""
    return {
        "target_type": "entrance_attempt",
        "target_id": str(attempt.pk),
        "target_label": program_label or attempt.exam.title,
        "subject_type": "student",
        "subject_id": str(attempt.student_id),
        "subject_label": student.full_name or "",
    }


def resolve_exam_access_token(token, *, active_only: bool = True) -> EntranceExam:
    """Resolve the shared exam token without granting student access."""
    queryset = EntranceExam.objects.all()
    if active_only:
        queryset = queryset.filter(is_active=True)
    return queryset.get(access_token=token)


def resolve_assigned_attempt(exam: EntranceExam, student: StudentProfile) -> EntranceAttempt:
    """Resolve one existing, exact student assignment for an exam token.

    The application and attempt relationships are checked together.  In
    particular, a valid cohort token never auto-assigns an application and a
    mismatched/corrupt relationship cannot be used as a public attempt.
    """
    if not exam.cohort_id:
        raise EntranceAttempt.DoesNotExist
    return EntranceAttempt.objects.select_related(
        "exam", "program_application", "student__user",
    ).get(
        exam=exam,
        student=student,
        program_application__student=student,
        program_application__cohort_id=exam.cohort_id,
        program_application__program_type=PROGRAM_TYPE_DIPLOMA,
        program_application__status__in=PUBLIC_TOKEN_APPLICATION_STATUSES,
    )


def resolve_submitted_attempt(exam: EntranceExam, student: StudentProfile) -> EntranceAttempt:
    """Resolve only the exact student's terminal attempt for read-only results."""
    if not exam.cohort_id:
        raise EntranceAttempt.DoesNotExist
    return EntranceAttempt.objects.select_related(
        "exam", "program_application", "student__user",
    ).filter(
        exam=exam,
        student=student,
        program_application__student=student,
        program_application__cohort_id=exam.cohort_id,
        program_application__program_type=PROGRAM_TYPE_DIPLOMA,
    ).filter(
        Q(submitted_at__isnull=False) | Q(status__in=TERMINAL_ATTEMPT_STATUSES),
    ).get()


def normalize_result_email_language(language_code: str | None) -> str:
    """Use the active request language when supported; Arabic is the fallback."""
    language = (language_code or "").lower().split("-", 1)[0]
    return language if language in ENTRANCE_RESULT_EMAIL_SUBJECTS else "ar"


def result_email_subject(language_code: str | None) -> str:
    return ENTRANCE_RESULT_EMAIL_SUBJECTS[normalize_result_email_language(language_code)]


def result_email_context(attempt: EntranceAttempt, language_code: str | None) -> dict[str, str]:
    language = normalize_result_email_language(language_code)
    return {
        "full_name": attempt.student.full_name or "",
        "exam_title": attempt.exam.title,
        "cohort_name": attempt.exam.cohort.name or "",
        "language_code": language,
        "subject": result_email_subject(language),
    }


def _validate_attempt_assignment(attempt: EntranceAttempt) -> None:
    """Protect service callers from an attempt with crossed object scope."""
    application = attempt.program_application
    if (
        not attempt.exam.cohort_id
        or application.program_type != PROGRAM_TYPE_DIPLOMA
        or application.student_id != attempt.student_id
        or application.cohort_id != attempt.exam.cohort_id
    ):
        raise ValueError(_("This entrance exam assignment is not valid."))


def _validate_attempt_application_state(attempt: EntranceAttempt) -> None:
    if attempt.program_application.status not in PUBLIC_TOKEN_APPLICATION_STATUSES:
        raise ValueError(_("This entrance exam access is no longer available."))


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
    if (
        exam.cohort_id is None
        or program_application.program_type != PROGRAM_TYPE_DIPLOMA
        or program_application.cohort_id != exam.cohort_id
        or program_application.student_id != student.pk
    ):
        raise ValueError(_("This entrance exam assignment is not valid."))
    attempt, _created = EntranceAttempt.objects.get_or_create(
        exam=exam,
        program_application=program_application,
        defaults={"student": student},
    )
    if attempt.student_id != student.pk:
        raise ValueError(_("This entrance exam assignment is not valid."))
    return attempt


@transaction.atomic
def start_attempt(attempt: EntranceAttempt) -> EntranceAttempt:
    locked = EntranceAttempt.objects.select_for_update().select_related(
        "exam", "program_application",
    ).get(pk=attempt.pk)
    _validate_attempt_assignment(locked)
    _validate_attempt_application_state(locked)
    if locked.submitted_at:
        raise ValueError(_("This exam has already been taken and cannot be started again."))
    # A retry of the same logical start returns the authoritative first start
    # instead of extending the timer or changing its timestamp.
    if locked.status == ATTEMPT_STATUS_IN_PROGRESS:
        return locked
    if locked.status != ATTEMPT_STATUS_ASSIGNED:
        raise ValueError(_("Attempt can only be started from assigned status."))
    if locked.program_application.status != PROGRAM_APP_STATUS_INVITED:
        raise ValueError(_("This exam cannot be started in the current application state."))
    exam = locked.exam
    now = timezone.now()
    if not exam.is_active:
        raise ValueError(_("Exam is not active."))
    if exam.open_at and now < exam.open_at:
        raise ValueError(_("Exam has not opened yet."))
    if exam.close_at and now > exam.close_at:
        raise ValueError(_("Exam has already closed."))
    locked.status = ATTEMPT_STATUS_IN_PROGRESS
    locked.started_at = now
    locked.save(update_fields=["status", "started_at", "updated_at"])
    return locked


def attempt_deadline(attempt: EntranceAttempt):
    """Return the authoritative deadline for an attempt, or None.

    The deadline is derived only from the first durable ``started_at``.  An
    unstarted attempt has no deadline; loading an instructions page cannot
    consume exam time.
    """
    exam = attempt.exam
    if not exam.duration_minutes or not attempt.started_at:
        return None
    return attempt.started_at + timezone.timedelta(minutes=exam.duration_minutes)


def submit_attempt(
    attempt: EntranceAttempt,
    answers: list[AnswerData],
    *,
    language_code: str | None = None,
) -> EntranceAttempt:
    with transaction.atomic():
        locked = (
            EntranceAttempt.objects.select_for_update(
                of=("self", "exam", "program_application"),
            )
            .select_related(
                "exam",
                "student",
                "program_application__student",
                "program_application__cohort__diploma",
                "program_application__course",
            )
            .get(pk=attempt.pk)
        )
        _validate_attempt_assignment(locked)
        # A replay after a durable terminal transition is a no-op.  The first
        # submission remains the only writer of answers and submitted_at.
        if locked.submitted_at or locked.status in (ATTEMPT_STATUS_COMPLETED, ATTEMPT_STATUS_GRADED):
            return locked
        if locked.status != ATTEMPT_STATUS_IN_PROGRESS:
            raise ValueError(_("Exam can only be submitted from in_progress status."))
        _validate_attempt_application_state(locked)
        if locked.program_application.status != PROGRAM_APP_STATUS_INVITED:
            raise ValueError(_("This exam cannot be submitted in the current application state."))
        exam = locked.exam
        now = timezone.now()
        if not exam.is_active:
            raise ValueError(_("Exam is not active."))
        if exam.open_at and now < exam.open_at:
            raise ValueError(_("Exam has not opened yet."))
        if exam.close_at and now > exam.close_at:
            raise ValueError(_("Exam has already closed."))
        deadline = attempt_deadline(locked)
        if deadline and now > deadline:
            raise ValueError(_("Exam duration has expired."))
        questions = list(exam.questions.all())
        question_map = {q.id: q for q in questions}
        # Answer coverage: every exam question must be present exactly once,
        # while a blank value remains an explicit unanswered response.
        answered_ids = {answer_data.question_id for answer_data in answers}
        unknown_ids = answered_ids - set(question_map)
        if unknown_ids:
            raise ValueError(_("Unknown question in submission."))
        if len(answers) != len(answered_ids):
            raise ValueError(_("Each exam question can only be answered once."))
        missing_ids = [q.id for q in questions if q.id not in answered_ids]
        if missing_ids:
            raise ValueError(_("Please answer all exam questions before submitting."))
        for answer_data in answers:
            question = question_map[answer_data.question_id]
            if question.question_type == QUESTION_TYPE_MCQ:
                if answer_data.written_text:
                    raise ValueError(_("MCQ answer must not carry written text."))
                choice = None
                if answer_data.selected_choice_id is not None:
                    choice = EntranceChoice.objects.filter(
                        id=answer_data.selected_choice_id, question=question,
                    ).first()
                    if choice is None:
                        raise ValueError(_("The selected choice does not belong to this question."))
                score = choice.score if choice else Decimal("0.00")
                EntranceAnswer.objects.update_or_create(
                    attempt=locked,
                    question=question,
                    defaults={"selected_choice": choice, "written_text": "", "score": score},
                )
            else:
                if answer_data.selected_choice_id is not None:
                    raise ValueError(_("Written answer must not carry a selected choice."))
                EntranceAnswer.objects.update_or_create(
                    attempt=locked,
                    question=question,
                    defaults={
                        "selected_choice": None,
                        "written_text": answer_data.written_text,
                        "score": None,
                    },
                )
        locked.status = ATTEMPT_STATUS_COMPLETED
        locked.submitted_at = now
        locked.save(update_fields=["status", "submitted_at", "updated_at"])
        _recompute_attempt_grade(locked, language_code=language_code)
    return locked


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


def grade_answer(
    answer: EntranceAnswer,
    score: Decimal,
    feedback: str,
    grader: User,
    *,
    language_code: str | None = None,
    attempt: EntranceAttempt | None = None,
) -> EntranceAnswer:
    validate_score(score)
    answer.score = score
    answer.feedback = feedback
    answer.graded_at = timezone.now()
    answer.graded_by = grader
    answer.save(update_fields=["score", "feedback", "graded_at", "graded_by"])
    # Callers that already loaded the attempt pass it so the resulting
    # recompute and audit snapshot do not reload the same row per answer.
    _recompute_attempt_grade(attempt or answer.attempt, actor=grader, language_code=language_code)
    return answer


def _recompute_attempt_grade(
    attempt: EntranceAttempt,
    actor: User | None = None,
    *,
    language_code: str | None = None,
) -> None:
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
        _update_program_application_from_result(
            attempt, attempt.result, actor=actor, language_code=language_code,
        )
    else:
        attempt.save(update_fields=["score"])


def _update_program_application_from_result(
    attempt: EntranceAttempt,
    result: str,
    actor: User | None = None,
    *,
    language_code: str | None = None,
) -> None:
    prog_app = attempt.program_application

    record_audit_event(
        actor=actor,
        action=ACTION_ENTRANCE_EXAM_COMPLETED,
        section=SECTION_ADMISSIONS,
        metadata={
            "application_id": str(attempt.program_application_id),
            "exam_id": attempt.exam_id,
            "score": str(attempt.score),
            "result": result,
            "pass_percentage": str(attempt.exam.pass_percentage),
        },
        **entrance_attempt_audit_fields(attempt),
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
        lambda: _send_result_email(
            attempt, result, actor, language_code=normalize_result_email_language(language_code),
        ),
    )


def _send_result_email(
    attempt: EntranceAttempt,
    result: str,
    actor: User | None = None,
    *,
    language_code: str | None = None,
) -> None:
    """Send the entrance result email after the submission commit.

    The email is best-effort: a send or log failure is logged and never
    raises into the caller (the submission has already committed). Uses the
    canonical queue pipeline so recipient, action, template, status, timestamps,
    actor and idempotency are handled centrally and Celery updates the log.
    """
    try:
        prog_app = attempt.program_application
        slug = TEMPLATE_ENTRANCE_EXAM_PASS if result == RESULT_PASS else TEMPLATE_ENTRANCE_EXAM_FAIL
        email_to = attempt.student.user.email
        context = result_email_context(attempt, language_code)
        queue_program_application_email(
            program_application=prog_app,
            action=EMAIL_ACTION_EXAM_RESULT,
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
    return assign_attempt(exam, program_application, program_application.student)


def queue_entrance_invitation_email(
    *,
    exam: EntranceExam,
    program_application: ProgramApplication,
    actor: User | None = None,
    is_resend: bool = False,
) -> tuple[object | None, object | None]:
    """Queue an entrance-exam invitation without changing application status.

    Invitation delivery is intentionally separate from the application
    lifecycle transition.  The post-activation application email helper also
    manages required intake forms; using it here can move an already-invited
    application back to ``survey_requested`` instead of sending this email.
    """
    with transaction.atomic():
        exam = EntranceExam.objects.select_for_update().select_related("cohort").get(pk=exam.pk)
        locked_application = ProgramApplication.objects.select_for_update().select_related(
            "student__user", "cohort",
        ).get(pk=program_application.pk)
        if locked_application.program_type != PROGRAM_TYPE_DIPLOMA:
            raise ValueError(_("Only diploma applications can receive an entrance exam invitation."))
        if locked_application.status != PROGRAM_APP_STATUS_INVITED:
            raise ValueError(_("This application is not eligible for an entrance exam invitation."))
        if not exam.is_active or not exam.cohort_id or locked_application.cohort_id != exam.cohort_id:
            raise ValueError(_("The entrance exam does not belong to this application cohort."))

        email_to = (locked_application.student.user.email or "").strip()
        if not email_to:
            return None, None
        # Invitation assignment is an Admin-controlled operation.  Public
        # token resolution never calls this path and can therefore never
        # create an attempt from a shared UUID.
        assign_attempt(exam, locked_application, locked_application.student)

        program_application = locked_application
    context = {
        "full_name": program_application.student.full_name or "",
        "email": email_to,
        "phone_number": program_application.student.phone or "",
        "exam_title": exam.title,
        "cohort_name": exam.cohort.name or "",
        "exam_url": reverse("entrance_exams:exam_access_token", args=[exam.access_token]) if exam.access_token else "",
        "exam_open_at": "",
        "exam_close_at": "",
    }
    if is_resend:
        resend_seq = ProgramApplicationEmailLog.objects.filter(
            program_application=program_application,
            action=EMAIL_ACTION_ENTRANCE_EXAM_INVITATION,
            is_resend=True,
        ).count()
        idempotency_key = (
            f"entrance_exam:resend:{EMAIL_ACTION_ENTRANCE_EXAM_INVITATION}:"
            f"app:{program_application.pk}:{resend_seq}"
        )
    else:
        idempotency_key = f"entrance_exam:{EMAIL_ACTION_ENTRANCE_EXAM_INVITATION}:app:{program_application.pk}"

    return queue_program_application_email(
        program_application=program_application,
        action=EMAIL_ACTION_ENTRANCE_EXAM_INVITATION,
        template_slug=TEMPLATE_ENTRANCE_EXAM_INVITATION,
        to_email=email_to,
        context=context,
        sent_by=actor,
        idempotency_key=idempotency_key,
        reference=f"app:{program_application.pk}",
        is_resend=is_resend,
    )


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
    with transaction.atomic():
        locked = EntranceAttempt.objects.select_for_update().select_related(
            "exam", "program_application",
        ).get(pk=attempt.pk)
        _validate_attempt_assignment(locked)
        if locked.status != ATTEMPT_STATUS_IN_PROGRESS:
            raise ValueError(_("Exam can only be saved from in_progress status."))
        _validate_attempt_application_state(locked)
        if locked.program_application.status != PROGRAM_APP_STATUS_INVITED:
            raise ValueError(_("This exam cannot be saved in the current application state."))
        exam = locked.exam
        now = timezone.now()
        if not exam.is_active:
            raise ValueError(_("Exam is not active."))
        if exam.open_at and now < exam.open_at:
            raise ValueError(_("Exam has not opened yet."))
        if exam.close_at and now > exam.close_at:
            raise ValueError(_("Exam has already closed."))
        deadline = attempt_deadline(locked)
        if deadline and now > deadline:
            raise ValueError(_("Exam duration has expired."))
        if not isinstance(written_text, str):
            raise ValueError(_("Invalid written answer."))
        question = exam.questions.filter(id=question_id).first()
        if not question:
            raise ValueError(_("Unknown question in snapshot."))
        if question.question_type == QUESTION_TYPE_MCQ:
            if written_text:
                raise ValueError(_("MCQ answer must not carry written text."))
            if choice_id is not None and not EntranceChoice.objects.filter(
                id=choice_id, question=question,
            ).exists():
                raise ValueError(_("The selected choice does not belong to this question."))
        elif choice_id is not None:
            raise ValueError(_("Written answer must not carry a selected choice."))
        EntranceSnapshot.objects.update_or_create(
            attempt=locked,
            question=question,
            defaults={
                "selected_choice_id": choice_id,
                "written_text": written_text,
            },
        )


def finalize_attempt_from_snapshots(
    attempt: EntranceAttempt,
    *,
    language_code: str | None = None,
) -> EntranceAttempt:
    """Rebuild answers from the latest snapshots and submit the attempt.

    Used when a student's browser fails to submit (timeout, crash, expiry)
    or an admin triggers recovery. Bypasses deadline checks. Idempotent:
    a terminal attempt is returned unchanged.
    """
    with transaction.atomic():
        locked = (
            EntranceAttempt.objects.select_for_update(
                of=("self", "exam", "program_application"),
            )
            .select_related(
                "exam",
                "student",
                "program_application__student",
                "program_application__cohort__diploma",
                "program_application__course",
            )
            .get(pk=attempt.pk)
        )
        if locked.submitted_at or locked.status in (ATTEMPT_STATUS_COMPLETED, ATTEMPT_STATUS_GRADED):
            return locked
        _validate_attempt_assignment(locked)
        if locked.status != ATTEMPT_STATUS_IN_PROGRESS:
            raise ValueError(_("Exam can only be finalized from in_progress status."))
        _validate_attempt_application_state(locked)
        if locked.program_application.status != PROGRAM_APP_STATUS_INVITED:
            raise ValueError(_("This exam cannot be finalized in the current application state."))
        return _finalize_locked_attempt(locked, language_code=language_code)


def _finalize_locked_attempt(
    locked: EntranceAttempt,
    *,
    language_code: str | None = None,
) -> EntranceAttempt:
    """Finalize an already locked in-progress attempt exactly once."""
    snapshots = list(
        EntranceSnapshot.objects.filter(attempt=locked).select_related("question")
    )
    if not snapshots:
        locked.status = ATTEMPT_STATUS_COMPLETED
        locked.submitted_at = timezone.now()
        locked.save(update_fields=["status", "submitted_at", "updated_at"])
        return locked

    questions = {
        q.id: q
        for q in locked.exam.questions.prefetch_related("choices").all()
    }
    validated_snapshots = []
    for snap in snapshots:
        question = questions.get(snap.question_id)
        if not question:
            raise ValueError(_("Snapshot question does not belong to this exam."))
        if question.question_type == QUESTION_TYPE_MCQ:
            if snap.written_text:
                raise ValueError(_("MCQ snapshot must not carry written text."))
            choice = None
            if snap.selected_choice_id is not None:
                choice = next(
                    (candidate for candidate in question.choices.all()
                     if candidate.id == snap.selected_choice_id),
                    None,
                )
                if choice is None:
                    raise ValueError(_("The selected choice does not belong to this question."))
            score = choice.score if choice else Decimal("0.00")
            validated_snapshots.append((question, choice, "", score))
        else:
            if snap.selected_choice_id is not None:
                raise ValueError(_("Written snapshot must not carry a selected choice."))
            validated_snapshots.append((question, None, snap.written_text, None))
    for question, choice, written_text, score in validated_snapshots:
        EntranceAnswer.objects.update_or_create(
            attempt=locked,
            question=question,
            defaults={
                "selected_choice": choice,
                "written_text": written_text,
                "score": score,
            },
        )
    locked.status = ATTEMPT_STATUS_COMPLETED
    locked.submitted_at = timezone.now()
    locked.save(update_fields=["status", "submitted_at", "updated_at"])
    _recompute_attempt_grade(locked, language_code=language_code)
    return locked


def finalize_expired_attempt(
    attempt: EntranceAttempt,
    *,
    language_code: str | None = None,
) -> EntranceAttempt:
    """Idempotently finalize an attempt once its deadline has passed.

    Reachable by the client at countdown zero. Only snapshots that reached
    the backend before the deadline are used — localStorage is never
    authoritative after expiry. Safe to call repeatedly: a terminal attempt
    is returned unchanged with its current result/state.
    """
    with transaction.atomic():
        locked = (
            EntranceAttempt.objects.select_for_update(
                of=("self", "exam", "program_application"),
            )
            .select_related(
                "exam",
                "student",
                "program_application__student",
                "program_application__cohort__diploma",
                "program_application__course",
            )
            .get(pk=attempt.pk)
        )
        if locked.submitted_at or locked.status in (ATTEMPT_STATUS_COMPLETED, ATTEMPT_STATUS_GRADED):
            return locked
        _validate_attempt_assignment(locked)
        if locked.status != ATTEMPT_STATUS_IN_PROGRESS:
            raise ValueError(_("Exam can only be finalized from in_progress status."))
        _validate_attempt_application_state(locked)
        if locked.program_application.status != PROGRAM_APP_STATUS_INVITED:
            raise ValueError(_("This exam cannot be finalized in the current application state."))
        deadline = attempt_deadline(locked)
        if deadline is None or timezone.now() <= deadline:
            raise ValueError(_("Exam duration has not expired yet."))
        return _finalize_locked_attempt(locked, language_code=language_code)


def finalize_all_pending_for_exam(
    exam: EntranceExam,
    *,
    language_code: str | None = None,
) -> int:
    """Finalize all in-progress attempts for an exam using their snapshots.

    Returns the count of finalized attempts.
    """
    attempts = EntranceAttempt.objects.filter(
        exam=exam, status=ATTEMPT_STATUS_IN_PROGRESS,
    )
    count = 0
    for attempt in attempts:
        finalize_attempt_from_snapshots(attempt, language_code=language_code)
        count += 1
    return count
