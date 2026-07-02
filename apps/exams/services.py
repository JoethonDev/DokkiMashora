from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.exams.constants import (
    ASSIGNMENT_STATUS_ASSIGNED,
    ASSIGNMENT_STATUS_COMPLETED,
    ASSIGNMENT_STATUS_GRADED,
    ASSIGNMENT_STATUS_IN_PROGRESS,
    QUESTION_TYPE_ESSAY,
    QUESTION_TYPE_MCQ,
)
from apps.exams.models import Answer, Assignment, Choice, Exam, ExamSnapshot


@dataclass
class ExamAnswerData:
    question_id: int
    selected_choice_id: int | None = None
    essay_text: str = ""


def assign_exam(exam: Exam, student, attempt: int = 1) -> Assignment:
    return Assignment.objects.create(
        exam=exam,
        student=student,
        attempt_number=attempt,
    )


def start_exam(assignment: Assignment) -> Assignment:
    if assignment.status != ASSIGNMENT_STATUS_ASSIGNED:
        raise ValueError(_("Exam can only be started from assigned status."))
    exam = assignment.exam
    now = timezone.now()
    if not exam.is_active:
        raise ValueError(_("Exam is not active."))
    if exam.open_at and now < exam.open_at:
        raise ValueError(_("Exam has not opened yet."))
    if exam.close_at and now > exam.close_at:
        raise ValueError(_("Exam has already closed."))
    assignment.status = ASSIGNMENT_STATUS_IN_PROGRESS
    assignment.started_at = now
    assignment.save(update_fields=["status", "started_at"])
    return assignment


def submit_exam(assignment: Assignment, answers: list[ExamAnswerData]) -> Assignment:
    if assignment.status != ASSIGNMENT_STATUS_IN_PROGRESS:
        raise ValueError(_("Exam can only be submitted from in_progress status."))
    exam = assignment.exam
    now = timezone.now()
    if not exam.is_active:
        raise ValueError(_("Exam is not active."))
    if exam.close_at and now > exam.close_at:
        raise ValueError(_("Exam has already closed."))
    if assignment.started_at and exam.duration_minutes:
        deadline = assignment.started_at + timezone.timedelta(minutes=exam.duration_minutes)
        if now > deadline:
            raise ValueError(_("Exam duration has expired."))
    questions = list(exam.questions.select_related("exam").prefetch_related("choices"))
    question_map = {q.id: q for q in questions}
    with transaction.atomic():
        for answer_data in answers:
            question = question_map[answer_data.question_id]
            if question.question_type == QUESTION_TYPE_MCQ and answer_data.essay_text:
                raise ValueError(_("MCQ answer must not carry essay text."))
            if question.question_type == QUESTION_TYPE_ESSAY and answer_data.selected_choice_id is not None:
                raise ValueError(_("Essay answer must not carry a selected choice."))
            kwargs = {"assignment": assignment, "question": question}
            if question.question_type == QUESTION_TYPE_MCQ:
                choice = (
                    Choice.objects.filter(id=answer_data.selected_choice_id, question=question)
                    .only("id", "is_correct")
                    .first()
                )
                kwargs["selected_choice"] = choice
                if choice and choice.is_correct:
                    kwargs["score"] = question.points
                else:
                    kwargs["score"] = Decimal("0.00")
            else:
                kwargs["essay_text"] = answer_data.essay_text
            Answer.objects.create(**kwargs)
        total = grade_mcq_assignment(assignment)
        assignment.status = ASSIGNMENT_STATUS_COMPLETED
        assignment.submitted_at = timezone.now()
        assignment.score = total
        assignment.save(update_fields=["status", "submitted_at", "score"])
    return assignment


def grade_mcq_assignment(assignment: Assignment) -> Decimal:
    total = Decimal("0.00")
    answers = assignment.answers.select_related("question").filter(
        selected_choice__is_correct=True,
        question__question_type=QUESTION_TYPE_MCQ,
    )
    for answer in answers:
        total += answer.question.points
    return total


def grade_essay_answer(answer: Answer, score: Decimal, feedback: str, grader) -> Answer:
    answer.score = score
    answer.feedback = feedback
    answer.graded_at = timezone.now()
    answer.graded_by = grader
    answer.save(update_fields=["score", "feedback", "graded_at", "graded_by"])
    _update_assignment_grade(answer.assignment)
    return answer


def _update_assignment_grade(assignment: Assignment):
    total = Decimal("0.00")
    all_graded = True
    for answer in assignment.answers.select_related("question").all():
        if answer.score is not None:
            total += answer.score
        else:
            all_graded = False
    assignment.score = total
    if all_graded:
        assignment.status = ASSIGNMENT_STATUS_GRADED
        assignment.graded_at = timezone.now()
        assignment.save(update_fields=["score", "status", "graded_at"])
    else:
        assignment.save(update_fields=["score"])


def get_student_assignments(student):
    return Assignment.objects.filter(student=student).select_related("exam")


def get_pending_grading():
    return Assignment.objects.filter(
        status=ASSIGNMENT_STATUS_COMPLETED,
    ).select_related("exam", "student")


def get_active_exams():
    now = timezone.now()
    return Exam.objects.filter(
        is_active=True,
        open_at__lte=now,
        close_at__gte=now,
    )


def get_exam_with_questions(exam_id: int) -> Exam | None:
    return (
        Exam.objects.prefetch_related(
            "questions__choices",
        )
        .filter(id=exam_id)
        .first()
    )


# ---------------------------------------------------------------------------
# Snapshot save & finalization
# ---------------------------------------------------------------------------


def save_snapshot(assignment: Assignment, question_id: int, choice_id: int | None = None, essay_text: str = "") -> None:
    """Save or update a single answer snapshot for the given assignment/question."""
    question = assignment.exam.questions.filter(id=question_id).first()
    if not question:
        return
    ExamSnapshot.objects.update_or_create(
        assignment=assignment,
        question=question,
        defaults={
            "selected_choice_id": choice_id,
            "essay_text": essay_text,
        },
    )


def finalize_assignment_from_snapshots(assignment: Assignment) -> Assignment:
    """Rebuild answers from the latest snapshots and submit the assignment.

    Used when a student's browser fails to submit (timeout, crash, etc.)
    and an admin triggers recovery. Bypasses deadline checks.
    """
    if assignment.status != ASSIGNMENT_STATUS_IN_PROGRESS:
        return assignment
    snapshots = ExamSnapshot.objects.filter(assignment=assignment).select_related("question")
    if not snapshots:
        assignment.status = ASSIGNMENT_STATUS_COMPLETED
        assignment.submitted_at = timezone.now()
        assignment.save(update_fields=["status", "submitted_at"])
        return assignment
    questions = {q.id: q for q in assignment.exam.questions.all()}
    with transaction.atomic():
        for snap in snapshots:
            question = questions.get(snap.question_id)
            if not question:
                continue
            if question.question_type == QUESTION_TYPE_MCQ:
                choice = Choice.objects.filter(
                    id=snap.selected_choice_id, question=question,
                ).first() if snap.selected_choice_id else None
                score = question.points if (choice and choice.is_correct) else Decimal("0.00")
                Answer.objects.update_or_create(
                    assignment=assignment,
                    question=question,
                    defaults={
                        "selected_choice": choice,
                        "score": score,
                    },
                )
            else:
                Answer.objects.update_or_create(
                    assignment=assignment,
                    question=question,
                    defaults={
                        "essay_text": snap.essay_text,
                    },
                )
        assignment.status = ASSIGNMENT_STATUS_COMPLETED
        assignment.submitted_at = timezone.now()
        assignment.score = grade_mcq_assignment(assignment)
        assignment.save(update_fields=["status", "submitted_at", "score"])
    return assignment


def finalize_all_pending_for_exam(exam: Exam) -> int:
    """Finalize all in-progress assignments for an exam using their snapshots.

    Returns the count of finalized assignments.
    """
    assignments = Assignment.objects.filter(
        exam=exam, status=ASSIGNMENT_STATUS_IN_PROGRESS,
    )
    count = 0
    for a in assignments:
        finalize_assignment_from_snapshots(a)
        count += 1
    return count
