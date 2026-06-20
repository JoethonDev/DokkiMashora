from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import Cohort, Diploma
from apps.academics.services import assign_moderator_to_students
from apps.accounts.constants import GROUP_MODERATOR
from apps.applications.models import Application, ProgramApplication
from apps.entrance_exams.constants import QUESTION_TYPE_MCQ, QUESTION_TYPE_WRITTEN
from apps.entrance_exams.models import EntranceAttempt, EntranceChoice, EntranceExam, EntranceQuestion
from apps.students.constants import EDUCATION_BACHELOR, GENDER_MALE
from apps.students.models import StudentProfile


@pytest.fixture
def admin_user(django_user_model):
    user = django_user_model.objects.create_superuser(email="admin@test.com", password="pass")
    return user


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
        passing_score=Decimal("50.00"),
        total_points=Decimal("100.00"),
        is_active=True,
        open_at=now - timezone.timedelta(days=1),
        close_at=now + timezone.timedelta(days=30),
        created_by=admin_user,
    )


@pytest.fixture
def admin_client(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.fixture
def student_client(client, student_user):
    client.force_login(student_user)
    return client


def _create_mcq_question(exam):
    q = EntranceQuestion.objects.create(
        exam=exam, question_type=QUESTION_TYPE_MCQ,
        text="Sample MCQ", points=Decimal("10.00"), order=1,
    )
    EntranceChoice.objects.create(question=q, text="Wrong", is_correct=False, order=1)
    EntranceChoice.objects.create(question=q, text="Correct", is_correct=True, order=2)
    return q


def _create_attempt(exam, student_profile):
    root = Application.objects.create(student=student_profile)
    prog_app = ProgramApplication.objects.create(
        student=student_profile, root_application=root, program_type="diploma",
        cohort=exam.cohort,
    )
    return EntranceAttempt.objects.create(
        exam=exam, program_application=prog_app, student=student_profile,
    )


# ---------------------------------------------------------------------------
# Student views
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStudentList:
    def test_requires_login(self, client):
        response = client.get(reverse("entrance_exams:student_list"))
        assert response.status_code == 302

    def test_shows_attempts(self, student_client, exam, student_profile):
        _create_attempt(exam, student_profile)
        response = student_client.get(reverse("entrance_exams:student_list"))
        assert response.status_code == 200
        assert exam.title in response.content.decode()

    def test_empty_list(self, student_client, student_profile):
        response = student_client.get(reverse("entrance_exams:student_list"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "entrance" in content.lower()


@pytest.mark.django_db
class TestExamInstructions:
    def test_requires_login(self, client, exam):
        response = client.get(reverse("entrance_exams:exam_instructions", args=[exam.id]))
        assert response.status_code == 302

    def test_shows_instructions(self, student_client, exam, student_profile):
        _create_attempt(exam, student_profile)
        response = student_client.get(reverse("entrance_exams:exam_instructions", args=[exam.id]))
        assert response.status_code == 200
        content = response.content.decode()
        assert exam.title in content

    def test_inactive_exam_404(self, student_client, exam):
        exam.is_active = False
        exam.save(update_fields=["is_active"])
        response = student_client.get(reverse("entrance_exams:exam_instructions", args=[exam.id]))
        assert response.status_code == 404

    def test_no_application_permission(self, student_client, exam, student_profile):
        response = student_client.get(reverse("entrance_exams:exam_instructions", args=[exam.id]))
        assert response.status_code == 403

    def test_no_attempt_auto_assigns(self, student_client, exam, student_profile):
        root = Application.objects.create(student=student_profile)
        ProgramApplication.objects.create(
            student=student_profile, root_application=root, program_type="diploma",
            cohort=exam.cohort,
        )
        response = student_client.get(reverse("entrance_exams:exam_instructions", args=[exam.id]))
        assert response.status_code == 200
        assert EntranceAttempt.objects.filter(exam=exam, student=student_profile).exists()

    def test_in_progress_redirects_to_take(self, student_client, exam, student_profile):
        attempt = _create_attempt(exam, student_profile)
        attempt.status = "in_progress"
        attempt.started_at = timezone.now()
        attempt.save(update_fields=["status", "started_at"])
        response = student_client.get(reverse("entrance_exams:exam_instructions", args=[exam.id]))
        assert response.status_code == 302
        assert reverse("entrance_exams:take_exam", args=[exam.id]) in response.url

    def test_completed_redirects_to_results(self, student_client, exam, student_profile):
        attempt = _create_attempt(exam, student_profile)
        attempt.status = "completed"
        attempt.save(update_fields=["status"])
        response = student_client.get(reverse("entrance_exams:exam_instructions", args=[exam.id]))
        assert response.status_code == 302
        assert reverse("entrance_exams:results", args=[exam.id]) in response.url


@pytest.mark.django_db
class TestStartExam:
    def test_requires_login(self, client, exam):
        response = client.post(reverse("entrance_exams:start_exam", args=[exam.id]))
        assert response.status_code == 302

    def test_start_exam_post(self, student_client, exam, student_profile):
        attempt = _create_attempt(exam, student_profile)
        response = student_client.post(reverse("entrance_exams:start_exam", args=[exam.id]))
        assert response.status_code == 302
        assert reverse("entrance_exams:take_exam", args=[exam.id]) in response.url
        attempt.refresh_from_db()
        assert attempt.status == "in_progress"

    def test_start_rejects_get(self, student_client, exam, student_profile):
        _create_attempt(exam, student_profile)
        response = student_client.get(reverse("entrance_exams:start_exam", args=[exam.id]))
        assert response.status_code == 405


@pytest.mark.django_db
class TestTakeExam:
    def test_requires_login(self, client, exam):
        response = client.get(reverse("entrance_exams:take_exam", args=[exam.id]))
        assert response.status_code == 302

    def test_shows_questions(self, student_client, exam, student_profile):
        attempt = _create_attempt(exam, student_profile)
        attempt.status = "in_progress"
        attempt.started_at = timezone.now()
        attempt.save(update_fields=["status", "started_at"])
        mcq = _create_mcq_question(exam)
        response = student_client.get(reverse("entrance_exams:take_exam", args=[exam.id]))
        assert response.status_code == 200
        assert mcq.text in response.content.decode()

    def test_not_in_progress_redirects(self, student_client, exam, student_profile):
        attempt = _create_attempt(exam, student_profile)
        attempt.status = "completed"
        attempt.save(update_fields=["status"])
        response = student_client.get(reverse("entrance_exams:take_exam", args=[exam.id]))
        assert response.status_code == 302
        assert reverse("entrance_exams:results", args=[exam.id]) in response.url


@pytest.mark.django_db
class TestSubmitExam:
    def test_requires_login(self, client, exam):
        response = client.post(reverse("entrance_exams:submit_exam", args=[exam.id]))
        assert response.status_code == 302

    def test_submit_mcq_correct(self, student_client, exam, student_profile):
        attempt = _create_attempt(exam, student_profile)
        attempt.status = "in_progress"
        attempt.started_at = timezone.now()
        attempt.save(update_fields=["status", "started_at"])
        mcq = _create_mcq_question(exam)
        correct = mcq.choices.get(is_correct=True)
        response = student_client.post(
            reverse("entrance_exams:submit_exam", args=[exam.id]),
            {f"question_{mcq.id}": str(correct.id)},
        )
        assert response.status_code == 302
        assert reverse("entrance_exams:results", args=[exam.id]) in response.url
        attempt.refresh_from_db()
        assert attempt.status == "graded"

    def test_submit_rejects_get(self, student_client, exam):
        response = student_client.get(reverse("entrance_exams:submit_exam", args=[exam.id]))
        assert response.status_code == 405


@pytest.mark.django_db
class TestExamResults:
    def test_requires_login(self, client, exam):
        response = client.get(reverse("entrance_exams:results", args=[exam.id]))
        assert response.status_code == 302

    def test_shows_results(self, student_client, exam, student_profile):
        attempt = _create_attempt(exam, student_profile)
        attempt.status = "graded"
        attempt.score = Decimal("10.00")
        attempt.result = "pass"
        attempt.save(update_fields=["status", "score", "result"])
        response = student_client.get(reverse("entrance_exams:results", args=[exam.id]))
        assert response.status_code == 200
        content = response.content.decode()
        assert "10" in content


# ---------------------------------------------------------------------------
# Admin views
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAdminList:
    def test_requires_login(self, client):
        response = client.get(reverse("entrance_exams:admin_list"))
        assert response.status_code == 302

    def test_requires_admin(self, student_client):
        response = student_client.get(reverse("entrance_exams:admin_list"))
        assert response.status_code == 403

    def test_lists_exams(self, admin_client, exam):
        response = admin_client.get(reverse("entrance_exams:admin_list"))
        assert response.status_code == 200
        assert exam.title in response.content.decode()


@pytest.mark.django_db
class TestAdminDetail:
    def test_requires_admin(self, student_client, exam):
        response = student_client.get(reverse("entrance_exams:admin_detail", args=[exam.id]))
        assert response.status_code == 403

    def test_shows_exam(self, admin_client, exam):
        response = admin_client.get(reverse("entrance_exams:admin_detail", args=[exam.id]))
        assert response.status_code == 200
        assert exam.title in response.content.decode()


@pytest.mark.django_db
class TestAdminCreateExam:
    def test_requires_admin(self, student_client):
        response = student_client.get(reverse("entrance_exams:admin_create_exam"))
        assert response.status_code == 403

    def test_create_get(self, admin_client):
        response = admin_client.get(reverse("entrance_exams:admin_create_exam"))
        assert response.status_code == 200
        assert "Create" in response.content.decode()

    def test_create_post(self, admin_client, cohort):
        now = timezone.now()
        response = admin_client.post(reverse("entrance_exams:admin_create_exam"), {
            "title": "New Exam",
            "diploma": cohort.diploma_id,
            "cohort": cohort.id,
            "duration_minutes": "30",
            "passing_score": "50.00",
            "open_at": (now - timezone.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
            "close_at": (now + timezone.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M"),
        })
        assert response.status_code == 302
        assert EntranceExam.objects.filter(title="New Exam").exists()


@pytest.mark.django_db
class TestAdminEditExam:
    def test_edit_get(self, admin_client, exam):
        response = admin_client.get(reverse("entrance_exams:admin_edit_exam", args=[exam.id]))
        assert response.status_code == 200
        assert exam.title in response.content.decode()

    def test_edit_post(self, admin_client, exam):
        response = admin_client.post(
            reverse("entrance_exams:admin_edit_exam", args=[exam.id]),
            {
                "title": "Updated Exam",
                "diploma": exam.diploma_id,
                "cohort": exam.cohort_id,
                "duration_minutes": "45",
                "passing_score": "60.00",
                "open_at": exam.open_at.strftime("%Y-%m-%dT%H:%M"),
                "close_at": exam.close_at.strftime("%Y-%m-%dT%H:%M"),
            },
        )
        assert response.status_code == 302
        exam.refresh_from_db()
        assert exam.title == "Updated Exam"


@pytest.mark.django_db
class TestAdminToggleActive:
    def test_toggle_active(self, admin_client, exam):
        assert exam.is_active is True
        response = admin_client.post(reverse("entrance_exams:admin_toggle_active", args=[exam.id]))
        assert response.status_code == 302
        exam.refresh_from_db()
        assert exam.is_active is False

    def test_requires_admin(self, student_client, exam):
        response = student_client.post(reverse("entrance_exams:admin_toggle_active", args=[exam.id]))
        assert response.status_code in (403, 405)


@pytest.mark.django_db
class TestAdminGradeList:
    def test_shows_pending(self, admin_client, exam, student_profile):
        attempt = _create_attempt(exam, student_profile)
        attempt.status = "completed"
        attempt.save(update_fields=["status"])
        response = admin_client.get(reverse("entrance_exams:admin_grade_list"))
        assert response.status_code == 200
        assert exam.title in response.content.decode()

    def test_moderator_sees_only_assigned_students(
        self,
        client,
        django_user_model,
        admin_user,
        exam,
        student_profile,
    ):
        moderator = django_user_model.objects.create_user(email="moderator@test.com", password="pass")
        group, _ = Group.objects.get_or_create(name=GROUP_MODERATOR)
        moderator.groups.add(group)
        other_user = django_user_model.objects.create_user(email="other-student@test.com", password="pass")
        other_profile = StudentProfile.objects.create(
            user=other_user,
            full_name="Other Student",
            date_of_birth=date(2000, 1, 1),
            gender=GENDER_MALE,
            address="Cairo",
            phone="01000000022",
            national_id="29001010100022",
            education=EDUCATION_BACHELOR,
        )
        visible = _create_attempt(exam, student_profile)
        hidden = _create_attempt(exam, other_profile)
        visible.status = "completed"
        hidden.status = "completed"
        visible.save(update_fields=["status"])
        hidden.save(update_fields=["status"])
        assign_moderator_to_students(moderator, [student_profile], exam.cohort, admin_user)
        client.force_login(moderator)

        response = client.get(reverse("entrance_exams:admin_grade_list"))

        assert response.status_code == 200
        content = response.content.decode()
        assert student_profile.user.email in content
        assert other_profile.user.email not in content


@pytest.mark.django_db
class TestAdminGradeAttempt:
    def test_grade_get(self, admin_client, exam, student_profile):
        attempt = _create_attempt(exam, student_profile)
        response = admin_client.get(reverse("entrance_exams:admin_grade_attempt", args=[attempt.id]))
        assert response.status_code == 200
        assert attempt.student.user.email in response.content.decode()

    def test_grade_post(self, admin_client, exam, student_profile):
        attempt = _create_attempt(exam, student_profile)
        q = EntranceQuestion.objects.create(
            exam=exam, question_type=QUESTION_TYPE_WRITTEN,
            text="Essay", points=Decimal("20.00"), order=1,
        )
        from apps.entrance_exams.models import EntranceAnswer
        EntranceAnswer.objects.create(attempt=attempt, question=q, written_text="My answer")
        response = admin_client.post(
            reverse("entrance_exams:admin_grade_attempt", args=[attempt.id]),
            {f"score_{q.id}": "15.00", f"feedback_{q.id}": "Good"},
        )
        assert response.status_code == 302
        attempt.refresh_from_db()
        assert attempt.status == "graded"
