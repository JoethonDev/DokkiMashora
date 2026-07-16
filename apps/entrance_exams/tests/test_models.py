from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError
from django.utils import timezone

from apps.academics.models import Cohort, Diploma
from apps.applications.models import ProgramApplication
from apps.entrance_exams.constants import (
    ATTEMPT_STATUS_ASSIGNED,
    QUESTION_TYPE_MCQ,
    QUESTION_TYPE_WRITTEN,
)
from apps.entrance_exams.models import (
    EntranceAnswer,
    EntranceAttempt,
    EntranceChoice,
    EntranceExam,
    EntranceQuestion,
)
from apps.students.constants import EDUCATION_BACHELOR, GENDER_MALE
from apps.students.models import StudentProfile


@pytest.fixture
def admin_user(django_user_model):
    return django_user_model.objects.create_superuser(email="admin@test.com", password="pass")


@pytest.fixture
def student_user(django_user_model):
    return django_user_model.objects.create_user(email="student@test.com", password="pass")


@pytest.fixture
def student_profile(student_user):
    return StudentProfile.objects.create(
        user=student_user,
        full_name="Test Student",
        date_of_birth=date(2000, 1, 1),
        gender=GENDER_MALE,
        address="Cairo",
        phone="01000000000",
        national_id="29001010100011",
        education=EDUCATION_BACHELOR,
    )


@pytest.fixture
def diploma(admin_user):
    return Diploma.objects.create(title="Test Diploma", created_by=admin_user)


@pytest.fixture
def cohort(diploma, admin_user):
    return Cohort.objects.create(
        diploma=diploma, name="C2025",
        start_date=timezone.now().date(), created_by=admin_user,
    )


@pytest.fixture
def exam(cohort, admin_user):
    now = timezone.now()
    return EntranceExam.objects.create(
        title="Test Entrance Exam",
        diploma=cohort.diploma,
        cohort=cohort,
        duration_minutes=30,
        pass_percentage=Decimal("50.00"),
        total_score=Decimal("100.00"),
        is_active=True,
        open_at=now - timezone.timedelta(days=1),
        close_at=now + timezone.timedelta(days=30),
        created_by=admin_user,
    )


@pytest.fixture
def mcq_question(exam):
    q = EntranceQuestion.objects.create(
        exam=exam, question_type=QUESTION_TYPE_MCQ,
        text="Sample MCQ", order=1,
    )
    EntranceChoice.objects.create(question=q, text="Wrong", score=Decimal("0.00"), order=1)
    EntranceChoice.objects.create(question=q, text="Correct", score=Decimal("10.00"), order=2)
    return q


@pytest.fixture
def written_question(exam):
    return EntranceQuestion.objects.create(
        exam=exam, question_type=QUESTION_TYPE_WRITTEN,
        text="Sample Written", order=2,
    )


@pytest.fixture
def prog_app(exam, student_profile):
    return ProgramApplication.objects.create(
        student=student_profile, program_type="diploma",
        cohort=exam.cohort,
    )


@pytest.fixture
def attempt(exam, prog_app, student_profile):
    return EntranceAttempt.objects.create(
        exam=exam, program_application=prog_app, student=student_profile,
    )


@pytest.mark.django_db
class TestEntranceExamModel:
    def test_create_exam(self, exam):
        assert exam.title == "Test Entrance Exam"
        assert exam.is_active
        assert str(exam) == f"{exam.title} — {exam.cohort}"

    def test_unique_exam_per_cohort(self, exam, cohort, admin_user):
        now = timezone.now()
        with pytest.raises(IntegrityError):
            EntranceExam.objects.create(
                title="Duplicate", diploma=cohort.diploma, cohort=cohort,
                duration_minutes=30, pass_percentage=Decimal("50.00"),
                is_active=True,
                open_at=now, close_at=now + timezone.timedelta(days=30),
                created_by=admin_user,
            )


@pytest.mark.django_db
class TestEntranceQuestionModel:
    def test_create_mcq(self, mcq_question):
        assert mcq_question.question_type == QUESTION_TYPE_MCQ
        assert mcq_question.choices.count() == 2

    def test_create_written(self, written_question):
        assert written_question.question_type == QUESTION_TYPE_WRITTEN
        assert written_question.choices.count() == 0

    def test_question_ordering(self, exam, mcq_question, written_question):
        questions = list(exam.questions.all())
        assert questions[0].order == 1
        assert questions[1].order == 2

    def test_unique_question_order_per_exam(self, exam, mcq_question):
        with pytest.raises(IntegrityError):
            EntranceQuestion.objects.create(
                exam=exam, question_type=QUESTION_TYPE_MCQ,
                text="Duplicate order", order=1,
            )

    def test_unique_choice_order_per_question(self, mcq_question):
        with pytest.raises(IntegrityError):
            EntranceChoice.objects.create(
                question=mcq_question, text="Dup", score=Decimal("0.00"), order=1,
            )

    def test_choice_score_default(self, mcq_question):
        choices = list(mcq_question.choices.all())
        wrong = next(c for c in choices if c.text == "Wrong")
        correct = next(c for c in choices if c.text == "Correct")
        assert wrong.score == Decimal("0.00")
        assert correct.score == Decimal("10.00")

    def test_question_str(self, mcq_question):
        assert str(mcq_question) == mcq_question.text[:50]

    def test_choice_str(self, mcq_question):
        choice = mcq_question.choices.first()
        assert str(choice) == choice.text[:50]


@pytest.mark.django_db
class TestEntranceAttemptModel:
    def test_default_status(self, attempt):
        assert attempt.status == ATTEMPT_STATUS_ASSIGNED

    def test_str(self, attempt):
        assert str(attempt) == f"{attempt.student} — {attempt.exam}"

    def test_unique_attempt_per_application_exam(self, attempt):
        with pytest.raises(IntegrityError):
            EntranceAttempt.objects.create(
                exam=attempt.exam,
                program_application=attempt.program_application,
                student=attempt.student,
            )


@pytest.mark.django_db
class TestEntranceAnswerModel:
    def test_create_mcq_answer(self, attempt, mcq_question):
        choice = mcq_question.choices.get(score__gt=0)
        answer = EntranceAnswer.objects.create(
            attempt=attempt, question=mcq_question,
            selected_choice=choice, score=Decimal("10.00"),
        )
        assert answer.selected_choice == choice
        assert answer.score == Decimal("10.00")
        assert str(answer) == f"Answer to Q{mcq_question.order} by {attempt.student}"

    def test_create_written_answer(self, attempt, written_question):
        answer = EntranceAnswer.objects.create(
            attempt=attempt, question=written_question,
            written_text="My answer",
        )
        assert answer.written_text == "My answer"
        assert answer.score is None

    def test_unique_answer_per_question(self, attempt, mcq_question):
        choice = mcq_question.choices.first()
        EntranceAnswer.objects.create(
            attempt=attempt, question=mcq_question, selected_choice=choice,
        )
        with pytest.raises(IntegrityError):
            EntranceAnswer.objects.create(
                attempt=attempt, question=mcq_question, selected_choice=choice,
            )
