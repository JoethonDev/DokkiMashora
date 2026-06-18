"""Tests for the token-based student exam flow."""

from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.exams.constants import (
    ASSIGNMENT_STATUS_ASSIGNED,
    ASSIGNMENT_STATUS_COMPLETED,
    ASSIGNMENT_STATUS_IN_PROGRESS,
    EXAM_TYPE_ENTRANCE,
    QUESTION_TYPE_MCQ,
)
from apps.exams.models import Assignment, Choice, Exam, Question
from apps.exams.services import ExamAnswerData, start_exam, submit_exam


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
def entrance_exam(admin_user):
    now = timezone.now()
    return Exam.objects.create(
        title="Entrance Exam",
        exam_type=EXAM_TYPE_ENTRANCE,
        duration_minutes=30,
        passing_score=Decimal("50.00"),
        total_points=Decimal("10.00"),
        is_active=True,
        open_at=now - timezone.timedelta(days=1),
        close_at=now + timezone.timedelta(days=30),
        created_by=admin_user,
    )


@pytest.fixture
def mcq_question(entrance_exam):
    q = Question.objects.create(
        exam=entrance_exam,
        question_type=QUESTION_TYPE_MCQ,
        text="Pick one",
        points=Decimal("10.00"),
        order=1,
    )
    Choice.objects.create(question=q, text="Wrong", is_correct=False, order=1)
    Choice.objects.create(question=q, text="Correct", is_correct=True, order=2)
    return q


@pytest.fixture
def student_client(client, student_user):
    client.force_login(student_user)
    return client


@pytest.mark.django_db
class TestExamAccess:
    def test_access_creates_assignment_and_redirects_to_instructions(
        self, student_client, student_user, entrance_exam
    ):
        response = student_client.get(reverse("exams:exam_access", args=[entrance_exam.access_token]))
        assert response.status_code == 302
        assert reverse("exams:exam_instructions_token", args=[entrance_exam.access_token]) in response.url
        assignment = Assignment.objects.get(exam=entrance_exam, student=student_user)
        assert assignment.status == ASSIGNMENT_STATUS_ASSIGNED

    def test_access_redirects_to_results_when_already_completed(
        self, student_client, student_user, entrance_exam, mcq_question
    ):
        assignment = Assignment.objects.create(
            exam=entrance_exam,
            student=student_user,
            status=ASSIGNMENT_STATUS_ASSIGNED,
        )
        start_exam(assignment)
        correct = mcq_question.choices.get(is_correct=True)
        submit_exam(
            assignment,
            [ExamAnswerData(question_id=mcq_question.id, selected_choice_id=correct.id)],
        )
        response = student_client.get(reverse("exams:exam_access", args=[entrance_exam.access_token]))
        assert response.status_code == 302
        assert reverse("exams:exam_results_token", args=[entrance_exam.access_token]) in response.url

    def test_access_redirects_to_take_when_in_progress(
        self, student_client, student_user, entrance_exam
    ):
        assignment = Assignment.objects.create(
            exam=entrance_exam,
            student=student_user,
            status=ASSIGNMENT_STATUS_ASSIGNED,
        )
        start_exam(assignment)
        response = student_client.get(reverse("exams:exam_access", args=[entrance_exam.access_token]))
        assert response.status_code == 302
        assert reverse("exams:take_exam_token", args=[entrance_exam.access_token]) in response.url


@pytest.mark.django_db
class TestTokenViews:
    def test_instructions_token_renders(self, student_client, student_user, entrance_exam):
        Assignment.objects.create(
            exam=entrance_exam,
            student=student_user,
            status=ASSIGNMENT_STATUS_ASSIGNED,
        )
        response = student_client.get(
            reverse("exams:exam_instructions_token", args=[entrance_exam.access_token])
        )
        assert response.status_code == 200
        assert entrance_exam.title in response.content.decode()

    def test_start_exam_token_starts_and_redirects(self, student_client, student_user, entrance_exam):
        Assignment.objects.create(
            exam=entrance_exam,
            student=student_user,
            status=ASSIGNMENT_STATUS_ASSIGNED,
        )
        response = student_client.post(
            reverse("exams:start_exam_token", args=[entrance_exam.access_token])
        )
        assert response.status_code == 302
        assert reverse("exams:take_exam_token", args=[entrance_exam.access_token]) in response.url
        assignment = Assignment.objects.get(exam=entrance_exam, student=student_user)
        assert assignment.status == ASSIGNMENT_STATUS_IN_PROGRESS

    def test_take_exam_token_renders(self, student_client, student_user, entrance_exam, mcq_question):
        assignment = Assignment.objects.create(
            exam=entrance_exam,
            student=student_user,
            status=ASSIGNMENT_STATUS_ASSIGNED,
        )
        start_exam(assignment)
        response = student_client.get(
            reverse("exams:take_exam_token", args=[entrance_exam.access_token])
        )
        assert response.status_code == 200
        assert mcq_question.text in response.content.decode()

    def test_submit_exam_token(self, student_client, student_user, entrance_exam, mcq_question):
        assignment = Assignment.objects.create(
            exam=entrance_exam,
            student=student_user,
            status=ASSIGNMENT_STATUS_ASSIGNED,
        )
        start_exam(assignment)
        correct = mcq_question.choices.get(is_correct=True)
        response = student_client.post(
            reverse("exams:submit_exam_token", args=[entrance_exam.access_token]),
            {f"question_{mcq_question.id}": correct.id},
        )
        assert response.status_code == 302
        assert reverse("exams:exam_results_token", args=[entrance_exam.access_token]) in response.url
        assignment.refresh_from_db()
        assert assignment.status == ASSIGNMENT_STATUS_COMPLETED

    def test_results_token_renders(self, student_client, student_user, entrance_exam, mcq_question):
        assignment = Assignment.objects.create(
            exam=entrance_exam,
            student=student_user,
            status=ASSIGNMENT_STATUS_ASSIGNED,
        )
        start_exam(assignment)
        correct = mcq_question.choices.get(is_correct=True)
        submit_exam(
            assignment,
            [ExamAnswerData(question_id=mcq_question.id, selected_choice_id=correct.id)],
        )
        response = student_client.get(
            reverse("exams:exam_results_token", args=[entrance_exam.access_token])
        )
        assert response.status_code == 200
        assert entrance_exam.title in response.content.decode()


@pytest.mark.django_db
class TestEntranceExamByIdForbidden:
    def test_exam_instructions_by_id_forbidden_for_entrance(
        self, student_client, student_user, entrance_exam
    ):
        Assignment.objects.create(
            exam=entrance_exam,
            student=student_user,
            status=ASSIGNMENT_STATUS_ASSIGNED,
        )
        response = student_client.get(reverse("exams:exam_instructions", args=[entrance_exam.id]))
        assert response.status_code == 403

    def test_take_exam_by_id_forbidden_for_entrance(
        self, student_client, student_user, entrance_exam
    ):
        Assignment.objects.create(
            exam=entrance_exam,
            student=student_user,
            status=ASSIGNMENT_STATUS_ASSIGNED,
        )
        response = student_client.get(reverse("exams:take_exam", args=[entrance_exam.id]))
        assert response.status_code == 403


@pytest.mark.django_db
class TestEntranceExamRetakeBlocked:
    def test_start_exam_token_blocked_after_completion(
        self, student_client, student_user, entrance_exam, mcq_question
    ):
        assignment = Assignment.objects.create(
            exam=entrance_exam,
            student=student_user,
            status=ASSIGNMENT_STATUS_ASSIGNED,
        )
        start_exam(assignment)
        correct = mcq_question.choices.get(is_correct=True)
        submit_exam(
            assignment,
            [ExamAnswerData(question_id=mcq_question.id, selected_choice_id=correct.id)],
        )
        response = student_client.post(
            reverse("exams:start_exam_token", args=[entrance_exam.access_token])
        )
        assert response.status_code == 302
        assert reverse("exams:exam_results_token", args=[entrance_exam.access_token]) in response.url

    def test_take_exam_token_redirects_completed_to_results(
        self, student_client, student_user, entrance_exam
    ):
        Assignment.objects.create(
            exam=entrance_exam,
            student=student_user,
            status=ASSIGNMENT_STATUS_COMPLETED,
        )
        response = student_client.get(
            reverse("exams:take_exam_token", args=[entrance_exam.access_token])
        )
        assert response.status_code == 302
        assert reverse("exams:exam_results_token", args=[entrance_exam.access_token]) in response.url
