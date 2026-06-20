"""Tests for the assessments app views."""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import Cohort, Course, CourseEnrollment, CourseVersion, Diploma
from apps.accounts.constants import GROUP_TEACHER
from apps.accounts.models import User
from apps.assessments.constants import (
    ASSESSMENT_TYPE_HOMEWORK,
    ASSESSMENT_TYPE_QUIZ,
    ATTEMPT_STATUS_IN_PROGRESS,
    ATTEMPT_STATUS_SUBMITTED,
    ATTEMPT_STATUS_UNDER_REVIEW,
    HOMEWORK_STATUS_UNDER_REVIEW,
)
from apps.assessments.models import Assessment, AssessmentAttempt, HomeworkSubmission
from apps.students.models import StudentProfile


def _create_profile(user: User, *, national_id: str, phone: str) -> StudentProfile:
    return StudentProfile.objects.create(
        user=user,
        full_name="Test Student",
        date_of_birth=date(2000, 1, 1),
        gender="male",
        address="Address",
        phone=phone,
        national_id=national_id,
        education="bachelor",
        specialization="Spec",
        employer="Employer",
        current_job="Job",
        church="Church",
        priest_name="Priest",
        priest_contact="01000000000",
    )


class StudentListViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(email="student@test.com", password="x")
        _create_profile(self.user, national_id="10000000000001", phone="01000000001")
        self.client.force_login(self.user)
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.first = Assessment.objects.create(
            title="First Quiz",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            open_at=timezone.now() - timezone.timedelta(days=1),
            close_at=timezone.now() + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            is_active=True,
            created_by=self.admin,
        )
        self.second = Assessment.objects.create(
            title="Second Quiz",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            open_at=timezone.now() - timezone.timedelta(days=1),
            close_at=timezone.now() + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            is_active=True,
            created_by=self.admin,
        )
        AssessmentAttempt.objects.create(
            assessment=self.first,
            student=self.user,
            attempt_number=1,
            status=ATTEMPT_STATUS_IN_PROGRESS,
        )
        AssessmentAttempt.objects.create(
            assessment=self.second,
            student=self.user,
            attempt_number=1,
            status=ATTEMPT_STATUS_SUBMITTED,
        )

    def test_latest_attempt_is_tracked_per_assessment(self):
        response = self.client.get(reverse("assessments:student_list"))
        self.assertEqual(response.status_code, 200)
        cards = response.context["assessment_cards"]
        first_card = next(card for card in cards if card["assessment"].id == self.first.id)
        second_card = next(card for card in cards if card["assessment"].id == self.second.id)
        self.assertEqual(first_card["latest_attempt"].status, ATTEMPT_STATUS_IN_PROGRESS)
        self.assertEqual(second_card["latest_attempt"].status, ATTEMPT_STATUS_SUBMITTED)


class AssessmentInstructionsViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.student = User.objects.create_user(email="student@test.com", password="x")
        _create_profile(self.student, national_id="10000000000002", phone="01000000002")
        self.client.force_login(self.student)
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.course = Course.objects.create(title="Course", created_by=self.admin)
        self.course_version = CourseVersion.objects.create(
            course=self.course,
            label="v1",
            created_by=self.admin,
        )
        self.assessment = Assessment.objects.create(
            title="Restricted Quiz",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            course_version=self.course_version,
            open_at=timezone.now() - timezone.timedelta(days=1),
            close_at=timezone.now() + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            is_active=True,
            created_by=self.admin,
        )

    def test_unenrolled_student_is_redirected(self):
        response = self.client.get(reverse("assessments:instructions", args=[self.assessment.id]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("assessments:student_list"), response.url)


class HomeworkTakeViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.student = User.objects.create_user(email="student@test.com", password="x")
        _create_profile(self.student, national_id="10000000000005", phone="01000000005")
        self.client.force_login(self.student)
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.course = Course.objects.create(title="Course", created_by=self.admin)
        self.course_version = CourseVersion.objects.create(
            course=self.course,
            label="v1",
            created_by=self.admin,
        )
        self.assessment = Assessment.objects.create(
            title="Homework",
            assessment_type=ASSESSMENT_TYPE_HOMEWORK,
            course_version=self.course_version,
            open_at=timezone.now() - timezone.timedelta(days=1),
            close_at=timezone.now() + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            is_active=True,
            created_by=self.admin,
        )

    def test_unenrolled_student_is_redirected_from_take_page(self):
        response = self.client.get(reverse("assessments:take", args=[self.assessment.id]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("assessments:student_list"), response.url)


class HomeworkSubmittedViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.student = User.objects.create_user(email="student2@test.com", password="x")
        _create_profile(self.student, national_id="10000000000006", phone="01000000006")
        self.client.force_login(self.student)
        self.admin = User.objects.create_superuser(email="admin2@test.com", password="x")
        self.course = Course.objects.create(title="Course", created_by=self.admin)
        self.course_version = CourseVersion.objects.create(
            course=self.course,
            label="v1",
            created_by=self.admin,
        )
        CourseEnrollment.objects.create(
            student=self.student.student_profile,
            course_version=self.course_version,
            status=ENROLLMENT_STATUS_ACTIVE,
        )
        self.assessment = Assessment.objects.create(
            title="Homework",
            assessment_type=ASSESSMENT_TYPE_HOMEWORK,
            course_version=self.course_version,
            open_at=timezone.now() - timezone.timedelta(days=1),
            close_at=timezone.now() + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            is_active=True,
            created_by=self.admin,
        )
        self.attempt = AssessmentAttempt.objects.create(
            assessment=self.assessment,
            student=self.student,
            attempt_number=1,
            status=ATTEMPT_STATUS_SUBMITTED,
        )
        self.submission = HomeworkSubmission.objects.create(
            assessment=self.assessment,
            student=self.student,
            attempt=self.attempt,
            original_filename="homework.pdf",
            storage_key="homework/1/homework.pdf",
            content_type="application/pdf",
            file_size=1024,
            submitted_at=timezone.now(),
            status=HOMEWORK_STATUS_UNDER_REVIEW,
        )

    def test_take_page_hides_upload_form_after_submission(self):
        response = self.client.get(reverse("assessments:take", args=[self.assessment.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Homework pending review")
        self.assertNotContains(response, "Upload your homework file")

    def test_results_page_exposes_download_link(self):
        response = self.client.get(reverse("assessments:results", args=[self.assessment.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Download uploaded file")


class AdminGradePermissionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(email="admin-grade@test.com", password="x")
        self.teacher = User.objects.create_user(email="unassigned-teacher@test.com", password="x")
        group, _ = Group.objects.get_or_create(name=GROUP_TEACHER)
        self.teacher.groups.add(group)
        self.student = User.objects.create_user(email="grade-student@test.com", password="x")
        _create_profile(self.student, national_id="10000000000106", phone="01000000106")
        diploma = Diploma.objects.create(title="Grade Diploma", total_semesters=2, created_by=self.admin)
        cohort = Cohort.objects.create(
            diploma=diploma,
            name="Grade Cohort",
            start_date=timezone.now().date(),
            created_by=self.admin,
        )
        course = Course.objects.create(title="Grade Course", created_by=self.admin)
        course_version = CourseVersion.objects.create(
            course=course,
            cohort=cohort,
            label="v1",
            created_by=self.admin,
        )
        self.assessment = Assessment.objects.create(
            title="Scoped Grade Quiz",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            course_version=course_version,
            open_at=timezone.now() - timezone.timedelta(days=1),
            close_at=timezone.now() + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            is_active=True,
            created_by=self.admin,
        )
        self.attempt = AssessmentAttempt.objects.create(
            assessment=self.assessment,
            student=self.student,
            attempt_number=1,
            status=ATTEMPT_STATUS_UNDER_REVIEW,
        )

    def test_unassigned_teacher_direct_grade_url_is_forbidden(self):
        self.client.force_login(self.teacher)

        response = self.client.get(reverse("assessments:admin_grade_detail", args=[self.attempt.id]))

        self.assertEqual(response.status_code, 403)
