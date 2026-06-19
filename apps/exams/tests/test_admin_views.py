"""Tests for the exams app admin views."""

from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import override as translation_override

from apps.exams.constants import QUESTION_TYPE_ESSAY, QUESTION_TYPE_MCQ
from apps.exams.models import Choice, Exam, Question


@pytest.fixture
def admin_user(django_user_model):
    user = django_user_model.objects.create_superuser(
        email="admin@test.com",
        password="pass",
    )
    return user


@pytest.fixture
def exam(admin_user):
    now = timezone.now()
    return Exam.objects.create(
        title="Test Exam",
        duration_minutes=30,
        passing_score=Decimal("50.00"),
        total_points=Decimal("30.00"),
        is_active=False,
        open_at=now - timezone.timedelta(days=1),
        close_at=now + timezone.timedelta(days=30),
        created_by=admin_user,
    )


@pytest.fixture
def admin_client(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.mark.django_db
class TestAdminDetail:
    def test_admin_detail_renders(self, admin_client, exam):
        response = admin_client.get(reverse("exams:admin_detail", args=[exam.id]))
        assert response.status_code == 200
        assert exam.title in response.content.decode()

    def test_admin_detail_qr_code_present_when_active(self, admin_client, exam):
        exam.is_active = True
        exam.save(update_fields=["is_active"])
        response = admin_client.get(reverse("exams:admin_detail", args=[exam.id]))
        content = response.content.decode()
        assert "svg" in content.lower()


@pytest.mark.django_db
class TestAdminQuestionForm:
    def test_question_form_create_renders(self, admin_client, exam):
        response = admin_client.get(
            reverse("exams:admin_question_form", args=[exam.id]),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert "choice_text_" in response.content.decode()

    def test_question_form_create_renders_full_page_without_htmx(self, admin_client, exam):
        response = admin_client.get(reverse("exams:admin_question_form", args=[exam.id]))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Add Question" in content
        assert "Back to exam" in content

    def test_question_form_edit_renders(self, admin_client, exam):
        question = Question.objects.create(
            exam=exam,
            question_type=QUESTION_TYPE_MCQ,
            text="MCQ",
            points=Decimal("5.00"),
            order=1,
        )
        response = admin_client.get(
            reverse("exams:admin_question_form", args=[exam.id, question.id]),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert "MCQ" in response.content.decode()

    def test_question_form_edit_renders_full_page_without_htmx(self, admin_client, exam):
        question = Question.objects.create(
            exam=exam,
            question_type=QUESTION_TYPE_MCQ,
            text="MCQ",
            points=Decimal("5.00"),
            order=1,
        )
        response = admin_client.get(reverse("exams:admin_question_form", args=[exam.id, question.id]))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Edit Question" in content
        assert "Back to exam" in content


@pytest.mark.django_db
class TestAdminQuestionSave:
    def test_create_mcq_question_with_choices(self, admin_client, exam):
        url = reverse("exams:admin_question_save", args=[exam.id])
        response = admin_client.post(
            url,
            {
                "text": "What is 2+2?",
                "question_type": QUESTION_TYPE_MCQ,
                "points": "5.00",
                "choice_text_new_1": "3",
                "choice_correct_new_1": "1",
                "choice_order_new_1": "1",
                "choice_text_new_2": "4",
                "choice_order_new_2": "2",
            },
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert "HX-Redirect" in response
        question = Question.objects.get(exam=exam)
        assert question.text == "What is 2+2?"
        assert question.choices.count() == 2
        assert question.choices.filter(is_correct=True, text="3").exists()

    def test_create_essay_question(self, admin_client, exam):
        url = reverse("exams:admin_question_save", args=[exam.id])
        response = admin_client.post(
            url,
            {
                "text": "Explain Django.",
                "question_type": QUESTION_TYPE_ESSAY,
                "points": "10.00",
            },
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert "HX-Redirect" in response
        question = Question.objects.get(exam=exam)
        assert question.question_type == QUESTION_TYPE_ESSAY
        assert question.choices.count() == 0

    def test_update_question_replaces_choices(self, admin_client, exam):
        question = Question.objects.create(
            exam=exam,
            question_type=QUESTION_TYPE_MCQ,
            text="Old",
            points=Decimal("5.00"),
            order=1,
        )
        old_choice = Choice.objects.create(
            question=question,
            text="Old choice",
            is_correct=False,
            order=1,
        )
        url = reverse("exams:admin_question_save", args=[exam.id, question.id])
        response = admin_client.post(
            url,
            {
                "text": "Updated",
                "question_type": QUESTION_TYPE_MCQ,
                "points": "5.00",
                "choice_text_new_1": "New choice A",
                "choice_correct_new_1": "1",
                "choice_order_new_1": "1",
                "choice_text_new_2": "New choice B",
                "choice_order_new_2": "2",
            },
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert "HX-Redirect" in response
        question.refresh_from_db()
        assert question.text == "Updated"
        assert not question.choices.filter(id=old_choice.id).exists()
        assert question.choices.filter(text="New choice A").exists()
        assert question.choices.filter(text="New choice B").exists()

    def test_mcq_requires_at_least_two_choices(self, admin_client, exam):
        with translation_override("en"):
            url = reverse("exams:admin_question_save", args=[exam.id])
            response = admin_client.post(
                url,
                {
                    "text": "No choices",
                    "question_type": QUESTION_TYPE_MCQ,
                    "points": "5.00",
                },
                HTTP_HX_REQUEST="true",
            )
        assert response.status_code == 200
        assert "HX-Redirect" not in response
        content = response.content.decode()
        assert "at least two choices" in content
        assert Question.objects.filter(exam=exam).count() == 0

    def test_mcq_requires_choices_on_plain_post(self, admin_client, exam):
        with translation_override("en"):
            url = reverse("exams:admin_question_save", args=[exam.id])
            response = admin_client.post(
                url,
                {
                    "text": "No choices",
                    "question_type": QUESTION_TYPE_MCQ,
                    "points": "5.00",
                },
            )
        assert response.status_code == 200
        assert "HX-Redirect" not in response
        content = response.content.decode()
        assert "Add Question" in content
        assert "at least two choices" in content

    def test_mcq_requires_exactly_one_correct_choice(self, admin_client, exam):
        with translation_override("en"):
            url = reverse("exams:admin_question_save", args=[exam.id])
            response = admin_client.post(
                url,
                {
                    "text": "Two correct",
                    "question_type": QUESTION_TYPE_MCQ,
                    "points": "5.00",
                    "choice_text_new_1": "A",
                    "choice_correct_new_1": "1",
                    "choice_order_new_1": "1",
                    "choice_text_new_2": "B",
                    "choice_correct_new_2": "1",
                    "choice_order_new_2": "2",
                },
                HTTP_HX_REQUEST="true",
            )
        assert response.status_code == 200
        assert "HX-Redirect" not in response
        content = response.content.decode()
        assert "Only one choice" in content
        assert Question.objects.filter(exam=exam).count() == 0

    def test_mcq_requires_a_correct_choice(self, admin_client, exam):
        with translation_override("en"):
            url = reverse("exams:admin_question_save", args=[exam.id])
            response = admin_client.post(
                url,
                {
                    "text": "No correct",
                    "question_type": QUESTION_TYPE_MCQ,
                    "points": "5.00",
                    "choice_text_new_1": "A",
                    "choice_order_new_1": "1",
                    "choice_text_new_2": "B",
                    "choice_order_new_2": "2",
                },
                HTTP_HX_REQUEST="true",
            )
        assert response.status_code == 200
        assert "HX-Redirect" not in response
        content = response.content.decode()
        assert "exactly one choice" in content
        assert Question.objects.filter(exam=exam).count() == 0


@pytest.mark.django_db
class TestAdminQuestionDelete:
    def test_delete_question(self, admin_client, exam):
        question = Question.objects.create(
            exam=exam,
            question_type=QUESTION_TYPE_ESSAY,
            text="To delete",
            points=Decimal("5.00"),
            order=1,
        )
        url = reverse("exams:admin_question_delete", args=[exam.id, question.id])
        response = admin_client.post(url, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert response["HX-Redirect"]
        assert not Question.objects.filter(id=question.id).exists()

