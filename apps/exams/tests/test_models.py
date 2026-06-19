"""Tests for the exams app models and services."""

from decimal import Decimal

import pytest
from django.utils import timezone

from apps.exams.constants import (
    ASSIGNMENT_STATUS_ASSIGNED,
    ASSIGNMENT_STATUS_COMPLETED,
    ASSIGNMENT_STATUS_GRADED,
    ASSIGNMENT_STATUS_IN_PROGRESS,
    QUESTION_TYPE_ESSAY,
    QUESTION_TYPE_MCQ,
)
from apps.exams.models import Answer, Assignment, Choice, Exam, Question
from apps.exams.services import (
    ExamAnswerData,
    assign_exam,
    get_pending_grading,
    grade_essay_answer,
    start_exam,
    submit_exam,
)


@pytest.fixture
def admin_user(django_user_model):
    return django_user_model.objects.create_superuser(
        email="admin@test.com",
        password="pass",
    )


@pytest.fixture
def student_user(django_user_model):
    return django_user_model.objects.create_user(
        email="student@test.com",
        password="pass",
    )


@pytest.fixture
def exam(admin_user):
    now = timezone.now()
    return Exam.objects.create(
        title="Test Entrance Exam",
        duration_minutes=30,
        passing_score=Decimal("50.00"),
        total_points=Decimal("30.00"),
        is_active=True,
        open_at=now - timezone.timedelta(days=1),
        close_at=now + timezone.timedelta(days=30),
        created_by=admin_user,
    )


@pytest.fixture
def mcq_question(exam):
    q = Question.objects.create(
        exam=exam,
        question_type=QUESTION_TYPE_MCQ,
        text="Sample MCQ",
        points=Decimal("10.00"),
        order=1,
    )
    Choice.objects.create(question=q, text="Wrong", is_correct=False, order=1)
    Choice.objects.create(question=q, text="Correct", is_correct=True, order=2)
    return q


@pytest.fixture
def essay_question(exam):
    return Question.objects.create(
        exam=exam,
        question_type=QUESTION_TYPE_ESSAY,
        text="Sample Essay",
        points=Decimal("20.00"),
        order=2,
    )


@pytest.fixture
def assignment(exam, student_user):
    return Assignment.objects.create(exam=exam, student=student_user)


@pytest.mark.django_db
class TestExamModel:
    def test_create_exam(self, exam):
        assert exam.title == "Test Entrance Exam"
        assert exam.is_active
        assert str(exam) == exam.title

    def test_exam_str(self, exam):
        assert str(exam) == exam.title


@pytest.mark.django_db
class TestQuestionModel:
    def test_create_mcq(self, mcq_question):
        assert mcq_question.question_type == QUESTION_TYPE_MCQ
        assert mcq_question.choices.count() == 2

    def test_create_essay(self, essay_question):
        assert essay_question.question_type == QUESTION_TYPE_ESSAY
        assert essay_question.choices.count() == 0

    def test_question_ordering(self, exam, mcq_question, essay_question):
        questions = list(exam.questions.all())
        assert questions[0].order == 1
        assert questions[1].order == 2

    def test_unique_question_order_per_exam(self, exam, mcq_question):
        from django.db import IntegrityError
        with pytest.raises(IntegrityError):
            Question.objects.create(
                exam=exam, question_type=QUESTION_TYPE_MCQ,
                text="Duplicate order", points=Decimal("5.00"), order=1,
            )

    def test_unique_choice_order_per_question(self, exam, mcq_question):
        from django.db import IntegrityError
        with pytest.raises(IntegrityError):
            Choice.objects.create(
                question=mcq_question, text="Dup", is_correct=False, order=1,
            )

    def test_essay_clean_deletes_choices_on_save(self, exam):
        q = Question.objects.create(
            exam=exam, question_type=QUESTION_TYPE_MCQ,
            text="Will become essay", points=Decimal("5.00"), order=10,
        )
        Choice.objects.create(question=q, text="A", is_correct=True, order=1)
        Choice.objects.create(question=q, text="B", is_correct=False, order=2)
        assert q.choices.count() == 2
        q.question_type = QUESTION_TYPE_ESSAY
        q.clean()
        q.save()
        assert q.choices.count() == 0


@pytest.mark.django_db
class TestAssignmentModel:
    def test_default_status(self, assignment):
        assert assignment.status == ASSIGNMENT_STATUS_ASSIGNED

    def test_is_pass_none_when_no_score(self, assignment):
        assert assignment.is_pass is None

    def test_is_pass_true(self, assignment):
        assignment.score = Decimal("60.00")
        assert assignment.is_pass is True

    def test_is_pass_false(self, assignment):
        assignment.score = Decimal("40.00")
        assert assignment.is_pass is False

    def test_str(self, assignment):
        assert str(assignment) == f"{assignment.student} - {assignment.exam}"


@pytest.mark.django_db
class TestServices:
    def test_assign_exam(self, exam, student_user):
        a = assign_exam(exam, student_user)
        assert a.status == ASSIGNMENT_STATUS_ASSIGNED
        assert a.student == student_user

    def test_start_exam(self, assignment):
        a = start_exam(assignment)
        assert a.status == ASSIGNMENT_STATUS_IN_PROGRESS
        assert a.started_at is not None

    def test_start_exam_wrong_status(self, assignment):
        start_exam(assignment)
        with pytest.raises(ValueError, match="assigned status"):
            start_exam(assignment)

    def test_submit_exam_mcq(self, assignment, mcq_question):
        start_exam(assignment)
        correct_choice = mcq_question.choices.get(is_correct=True)
        answers_data = [
            ExamAnswerData(question_id=mcq_question.id, selected_choice_id=correct_choice.id),
        ]
        a = submit_exam(assignment, answers_data)
        assert a.status == ASSIGNMENT_STATUS_COMPLETED
        assert a.submitted_at is not None
        assert a.score == Decimal("10.00")

    def test_submit_exam_mcq_wrong(self, assignment, mcq_question):
        start_exam(assignment)
        wrong_choice = mcq_question.choices.get(is_correct=False)
        answers_data = [
            ExamAnswerData(question_id=mcq_question.id, selected_choice_id=wrong_choice.id),
        ]
        a = submit_exam(assignment, answers_data)
        assert a.score == Decimal("0.00")

    def test_submit_exam_essay(self, assignment, essay_question):
        start_exam(assignment)
        answers_data = [
            ExamAnswerData(question_id=essay_question.id, essay_text="My essay answer"),
        ]
        a = submit_exam(assignment, answers_data)
        assert a.status == ASSIGNMENT_STATUS_COMPLETED
        assert Answer.objects.filter(assignment=a, question=essay_question).exists()
        ans = Answer.objects.get(assignment=a, question=essay_question)
        assert ans.essay_text == "My essay answer"
        assert ans.score is None

    def test_grade_essay(self, assignment, essay_question, admin_user):
        start_exam(assignment)
        answers_data = [
            ExamAnswerData(question_id=essay_question.id, essay_text="Great answer"),
        ]
        a = submit_exam(assignment, answers_data)
        ans = Answer.objects.get(assignment=a, question=essay_question)
        grade_essay_answer(ans, Decimal("15.00"), "Good job", admin_user)
        ans.refresh_from_db()
        assert ans.score == Decimal("15.00")
        assert ans.feedback == "Good job"
        assert ans.graded_by == admin_user
        a.refresh_from_db()
        assert a.status == ASSIGNMENT_STATUS_GRADED

    def test_get_pending_grading(self, assignment):
        assert list(get_pending_grading()) == []
        start_exam(assignment)
        assignment.status = ASSIGNMENT_STATUS_COMPLETED
        assignment.save(update_fields=["status"])
        pending = list(get_pending_grading())
        assert len(pending) == 1
        assert pending[0] == assignment

    def test_submit_exam_essay_rejects_selected_choice(self, assignment, essay_question):
        start_exam(assignment)
        with pytest.raises(ValueError, match="must not carry a selected choice"):
            submit_exam(assignment, [
                ExamAnswerData(question_id=essay_question.id, essay_text="Essay",
                               selected_choice_id=999),
            ])

    def test_submit_exam_mcq_rejects_essay_text(self, assignment, mcq_question):
        start_exam(assignment)
        with pytest.raises(ValueError, match="must not carry essay text"):
            submit_exam(assignment, [
                ExamAnswerData(question_id=mcq_question.id, selected_choice_id=1,
                               essay_text="Should not be here"),
            ])

    def test_submit_exam_wrong_status(self, assignment):
        with pytest.raises(ValueError, match="in_progress status"):
            submit_exam(assignment, [])

    def test_submit_exam_mixed(self, assignment, mcq_question):
        start_exam(assignment)
        correct_choice = mcq_question.choices.get(is_correct=True)
        answers_data = [
            ExamAnswerData(question_id=mcq_question.id, selected_choice_id=correct_choice.id),
        ]
        a = submit_exam(assignment, answers_data)
        assert a.score == Decimal("10.00")
