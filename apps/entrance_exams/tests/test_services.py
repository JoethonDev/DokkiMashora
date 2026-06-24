from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.academics.models import Cohort, Diploma
from apps.applications.constants import (
    PROGRAM_APP_STATUS_REJECTED,
)
from apps.applications.models import Application, ProgramApplication
from apps.entrance_exams.constants import (
    ATTEMPT_STATUS_ASSIGNED,
    ATTEMPT_STATUS_COMPLETED,
    ATTEMPT_STATUS_GRADED,
    ATTEMPT_STATUS_IN_PROGRESS,
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
from apps.entrance_exams.services import (
    AnswerData,
    assign_attempt,
    assign_exam_for_application,
    get_pending_grading,
    get_student_attempts,
    grade_answer,
    start_attempt,
    submit_attempt,
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
def another_student(django_user_model):
    user = django_user_model.objects.create_user(email="other@test.com", password="pass")
    return StudentProfile.objects.create(
        user=user,
        full_name="Other Student",
        date_of_birth=date(2000, 1, 1),
        gender=GENDER_MALE,
        address="Giza",
        phone="02000000000",
        national_id="39001010100011",
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
        passing_score=Decimal("50.00"),
        total_points=Decimal("100.00"),
        is_active=True,
        open_at=now - timezone.timedelta(days=1),
        close_at=now + timezone.timedelta(days=30),
        created_by=admin_user,
    )


@pytest.fixture
def mcq_question(exam):
    q = EntranceQuestion.objects.create(
        exam=exam, question_type=QUESTION_TYPE_MCQ,
        text="Sample MCQ", points=Decimal("10.00"), order=1,
    )
    EntranceChoice.objects.create(question=q, text="Wrong", score=0, order=1)
    EntranceChoice.objects.create(question=q, text="Correct", score=Decimal("10.00"), order=2)
    return q


@pytest.fixture
def written_question(exam):
    return EntranceQuestion.objects.create(
        exam=exam, question_type=QUESTION_TYPE_WRITTEN,
        text="Sample Written", points=Decimal("20.00"), order=2,
    )


@pytest.fixture
def prog_app(exam, student_profile):
    root = Application.objects.create(student=student_profile)
    return ProgramApplication.objects.create(
        student=student_profile, root_application=root, program_type="diploma",
        cohort=exam.cohort,
    )


@pytest.fixture
def attempt(exam, prog_app, student_profile):
    return EntranceAttempt.objects.create(
        exam=exam, program_application=prog_app, student=student_profile,
    )


@pytest.mark.django_db
class TestAssignAttempt:
    def test_assign_attempt(self, exam, prog_app, student_profile):
        a = assign_attempt(exam, prog_app, student_profile)
        assert a.status == ATTEMPT_STATUS_ASSIGNED
        assert a.exam == exam
        assert a.student == student_profile

    def test_assign_exam_for_application_diploma(self, exam, prog_app, student_profile):
        a = assign_exam_for_application(prog_app)
        assert a is not None
        assert a.exam == exam
        assert a.student == student_profile
        assert a.status == ATTEMPT_STATUS_ASSIGNED

    def test_assign_exam_for_application_course_type(self, student_profile):
        root = Application.objects.create(student=student_profile)
        course_app = ProgramApplication.objects.create(
            student=student_profile, root_application=root, program_type="course",
        )
        assert assign_exam_for_application(course_app) is None

    def test_assign_exam_for_application_no_exam(self, cohort, student_profile):
        root = Application.objects.create(student=student_profile)
        app = ProgramApplication.objects.create(
            student=student_profile, root_application=root, program_type="diploma",
            cohort=cohort,
        )
        assert assign_exam_for_application(app) is None

    def test_assign_exam_for_application_idempotent(self, exam, prog_app, student_profile):
        a1 = assign_exam_for_application(prog_app)
        a2 = assign_exam_for_application(prog_app)
        assert a1 is not None
        assert a2 is not None
        assert a1.pk == a2.pk


@pytest.mark.django_db
class TestStartAttempt:
    def test_start_attempt(self, attempt):
        a = start_attempt(attempt)
        assert a.status == ATTEMPT_STATUS_IN_PROGRESS
        assert a.started_at is not None

    def test_start_attempt_wrong_status(self, attempt):
        start_attempt(attempt)
        with pytest.raises(ValueError, match="assigned status"):
            start_attempt(attempt)

    def test_start_attempt_inactive_exam(self, attempt):
        attempt.exam.is_active = False
        attempt.exam.save(update_fields=["is_active"])
        with pytest.raises(ValueError, match="not active"):
            start_attempt(attempt)

    def test_start_attempt_not_open(self, attempt):
        attempt.exam.open_at = timezone.now() + timezone.timedelta(hours=1)
        attempt.exam.save(update_fields=["open_at"])
        with pytest.raises(ValueError, match="not opened"):
            start_attempt(attempt)

    def test_start_attempt_already_closed(self, attempt):
        attempt.exam.close_at = timezone.now() - timezone.timedelta(hours=1)
        attempt.exam.save(update_fields=["close_at"])
        with pytest.raises(ValueError, match="already closed"):
            start_attempt(attempt)


@pytest.mark.django_db
class TestSubmitAttempt:
    def test_submit_mcq_correct(self, attempt, mcq_question):
        start_attempt(attempt)
        correct_choice = mcq_question.choices.get(score__gt=0)
        answers = [AnswerData(question_id=mcq_question.id, selected_choice_id=correct_choice.id)]
        a = submit_attempt(attempt, answers)
        assert a.status == ATTEMPT_STATUS_GRADED
        assert a.submitted_at is not None
        assert a.score == Decimal("10.00")

    def test_submit_mcq_wrong(self, attempt, mcq_question):
        start_attempt(attempt)
        wrong_choice = mcq_question.choices.get(score=0)
        answers = [AnswerData(question_id=mcq_question.id, selected_choice_id=wrong_choice.id)]
        a = submit_attempt(attempt, answers)
        assert a.score == Decimal("0.00")
        assert a.result == "fail"

    def test_submit_written(self, attempt, written_question):
        start_attempt(attempt)
        answers = [AnswerData(question_id=written_question.id, written_text="My essay answer")]
        a = submit_attempt(attempt, answers)
        assert a.status == ATTEMPT_STATUS_COMPLETED
        assert EntranceAnswer.objects.filter(attempt=a, question=written_question).exists()
        ans = EntranceAnswer.objects.get(attempt=a, question=written_question)
        assert ans.written_text == "My essay answer"
        assert ans.score is None

    def test_submit_mixed_questions(self, attempt, mcq_question, written_question):
        start_attempt(attempt)
        correct_choice = mcq_question.choices.get(score__gt=0)
        answers = [
            AnswerData(question_id=mcq_question.id, selected_choice_id=correct_choice.id),
            AnswerData(question_id=written_question.id, written_text="My answer"),
        ]
        a = submit_attempt(attempt, answers)
        assert a.status == ATTEMPT_STATUS_COMPLETED
        assert a.score == Decimal("10.00")

    def test_submit_mcq_with_written_text_raises(self, attempt, mcq_question):
        start_attempt(attempt)
        with pytest.raises(ValueError, match="must not carry written text"):
            submit_attempt(attempt, [AnswerData(question_id=mcq_question.id, selected_choice_id=1, written_text="bad")])

    def test_submit_written_with_choice_raises(self, attempt, written_question):
        start_attempt(attempt)
        with pytest.raises(ValueError, match="must not carry a selected choice"):
            submit_attempt(attempt, [AnswerData(question_id=written_question.id, selected_choice_id=999)])

    def test_submit_wrong_status(self, attempt):
        with pytest.raises(ValueError, match="in_progress status"):
            submit_attempt(attempt, [])

    def test_submit_exam_closed(self, attempt):
        start_attempt(attempt)
        attempt.exam.close_at = timezone.now() - timezone.timedelta(hours=1)
        attempt.exam.save(update_fields=["close_at"])
        with pytest.raises(ValueError, match="already closed"):
            submit_attempt(attempt, [])

    def test_submit_exam_expired_duration(self, attempt):
        start_attempt(attempt)
        attempt.started_at = timezone.now() - timezone.timedelta(minutes=31)
        attempt.save(update_fields=["started_at"])
        with pytest.raises(ValueError, match="duration has expired"):
            submit_attempt(attempt, [])

    def test_submit_no_answers(self, attempt):
        start_attempt(attempt)
        a = submit_attempt(attempt, [])
        assert a.status == ATTEMPT_STATUS_GRADED
        assert a.score == Decimal("0.00")
        assert a.result == "fail"


@pytest.mark.django_db
class TestGradeAnswer:
    def test_grade_written_answer(self, attempt, written_question, admin_user):
        start_attempt(attempt)
        submit_attempt(attempt, [AnswerData(question_id=written_question.id, written_text="Great answer")])
        ans = EntranceAnswer.objects.get(attempt=attempt, question=written_question)
        grade_answer(ans, Decimal("15.00"), "Good job", admin_user)
        ans.refresh_from_db()
        assert ans.score == Decimal("15.00")
        assert ans.feedback == "Good job"
        assert ans.graded_by == admin_user
        attempt.refresh_from_db()
        assert attempt.status == ATTEMPT_STATUS_GRADED

    def test_grade_all_mcq_auto(self, attempt, mcq_question):
        start_attempt(attempt)
        correct_choice = mcq_question.choices.get(score__gt=0)
        submit_attempt(attempt, [AnswerData(question_id=mcq_question.id, selected_choice_id=correct_choice.id)])
        attempt.refresh_from_db()
        assert attempt.status == ATTEMPT_STATUS_GRADED
        assert attempt.score == Decimal("10.00")

    def test_program_application_updated_on_pass(self, exam, attempt, mcq_question):
        EntranceQuestion.objects.create(
            exam=exam, question_type=QUESTION_TYPE_MCQ,
            text="Extra MCQ", points=Decimal("50.00"), order=3,
        )
        start_attempt(attempt)
        correct_choice = mcq_question.choices.get(score__gt=0)
        submit_attempt(attempt, [AnswerData(question_id=mcq_question.id, selected_choice_id=correct_choice.id)])
        attempt.refresh_from_db()
        assert attempt.score == Decimal("10.00")

    def test_program_application_updated_on_fail(self, attempt, mcq_question):
        start_attempt(attempt)
        wrong_choice = mcq_question.choices.get(score=0)
        submit_attempt(attempt, [AnswerData(question_id=mcq_question.id, selected_choice_id=wrong_choice.id)])
        attempt.program_application.refresh_from_db()
        assert attempt.program_application.status == PROGRAM_APP_STATUS_REJECTED


@pytest.mark.django_db
class TestQueryHelpers:
    def test_get_student_attempts(self, attempt, student_profile):
        results = get_student_attempts(student_profile)
        assert len(results) == 1
        assert results[0] == attempt

    def test_get_student_attempts_empty(self, another_student):
        results = get_student_attempts(another_student)
        assert results == []

    def test_get_pending_grading(self, attempt, mcq_question):
        assert list(get_pending_grading()) == []
        start_attempt(attempt)
        correct_choice = mcq_question.choices.get(score__gt=0)
        submit_attempt(attempt, [AnswerData(question_id=mcq_question.id, selected_choice_id=correct_choice.id)])
        assert list(get_pending_grading()) == []

    def test_get_pending_grading_written(self, attempt, written_question):
        assert list(get_pending_grading()) == []
        start_attempt(attempt)
        submit_attempt(attempt, [AnswerData(question_id=written_question.id, written_text="Pending")])
        pending = list(get_pending_grading())
        assert len(pending) == 1
        assert pending[0] == attempt


@pytest.mark.django_db
class TestScoringWithScores:
    """Phase 9: MCQ choices have numeric scores, not just correct/incorrect."""

    def test_choice_score_affects_attempt_score(self, exam, student_profile, admin_user):
        """Choice A (score 3) + Choice B (score 7) → selecting each gives correct partial score."""
        root = Application.objects.create(student=student_profile)
        prog_app = ProgramApplication.objects.create(
            student=student_profile, root_application=root, program_type="diploma",
            cohort=exam.cohort,
        )
        q = EntranceQuestion.objects.create(
            exam=exam, question_type=QUESTION_TYPE_MCQ,
            text="Score test", points=Decimal("10.00"), order=99,
        )
        choice_a = EntranceChoice.objects.create(question=q, text="A", score=Decimal("3.00"), order=1)
        choice_b = EntranceChoice.objects.create(question=q, text="B", score=Decimal("7.00"), order=2)
        choice_c = EntranceChoice.objects.create(question=q, text="C", score=Decimal("0.00"), order=3)

        attempt = EntranceAttempt.objects.create(
            exam=exam, program_application=prog_app, student=student_profile,
        )
        start_attempt(attempt)

        # Select only choice A → score should be 3
        a = submit_attempt(attempt, [AnswerData(question_id=q.id, selected_choice_id=choice_a.id)])
        assert a.score == Decimal("3.00")

    def test_multiple_choice_scores_sum_correctly(self, exam, student_profile, admin_user):
        """Multiple questions: choice scores sum per question, not across questions."""
        root = Application.objects.create(student=student_profile)
        prog_app = ProgramApplication.objects.create(
            student=student_profile, root_application=root, program_type="diploma",
            cohort=exam.cohort,
        )
        q1 = EntranceQuestion.objects.create(
            exam=exam, question_type=QUESTION_TYPE_MCQ,
            text="Q1", points=Decimal("5.00"), order=1,
        )
        q2 = EntranceQuestion.objects.create(
            exam=exam, question_type=QUESTION_TYPE_MCQ,
            text="Q2", points=Decimal("15.00"), order=2,
        )
        c1 = EntranceChoice.objects.create(question=q1, text="Q1 correct", score=Decimal("5.00"), order=1)
        c2 = EntranceChoice.objects.create(question=q2, text="Q2 correct", score=Decimal("15.00"), order=1)
        EntranceChoice.objects.create(question=q2, text="Q2 wrong", score=Decimal("0.00"), order=2)

        attempt = EntranceAttempt.objects.create(
            exam=exam, program_application=prog_app, student=student_profile,
        )
        start_attempt(attempt)
        a = submit_attempt(attempt, [
            AnswerData(question_id=q1.id, selected_choice_id=c1.id),
            AnswerData(question_id=q2.id, selected_choice_id=c2.id),
        ])
        assert a.score == Decimal("20.00")


@pytest.mark.django_db
class TestScoreVisibility:
    """Phase 9: Choice scores must never render on the take-exam page."""

    def test_choice_score_is_not_in_take_exam_context(self, rf, exam, student_profile, admin_user):
        from apps.entrance_exams.views import take_exam

        q = EntranceQuestion.objects.create(
            exam=exam, question_type=QUESTION_TYPE_MCQ,
            text="Hidden score test", points=Decimal("5.00"), order=1,
        )
        EntranceChoice.objects.create(question=q, text="Option with score", score=Decimal("10.00"), order=1)

        root = Application.objects.create(student=student_profile)
        prog_app = ProgramApplication.objects.create(
            student=student_profile, root_application=root, program_type="diploma",
            cohort=exam.cohort,
        )
        EntranceAttempt.objects.create(
            exam=exam, program_application=prog_app, student=student_profile, status="in_progress",
            started_at=timezone.now(),
        )
        request = rf.get("/")
        request.user = student_profile.user
        response = take_exam(request, exam.id)
        html = response.content.decode()
        assert "10.00 pts" not in html, "Choice score value must not appear in take-exam HTML"
