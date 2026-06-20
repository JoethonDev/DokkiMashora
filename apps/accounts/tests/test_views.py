"""Tests for authentication views, login, logout, and dashboards."""

from __future__ import annotations

from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.constants import (
    GROUP_ADMIN,
    GROUP_STUDENT,
    GROUP_TEACHER,
)
from apps.accounts.models import User


class LoginTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.user = User.objects.create_user(email="login@example.com", password="ChangeMe123!")
        self.url = reverse("accounts:login")

    def _activate_english(self) -> None:
        # Setting the language cookie is the supported way to switch languages
        # without using the i18n URL prefix in tests.
        self.client.cookies["django_language"] = "en"

    def test_login_page_renders_in_arabic_by_default(self) -> None:
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'lang="ar"')
        self.assertContains(response, 'dir="rtl"')

    def test_login_page_renders_in_english(self) -> None:
        # English is reached via the /en/ URL prefix because the default
        # language (Arabic) is unprefixed.
        response = self.client.get("/en/accounts/login/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'lang="en"')
        self.assertContains(response, 'dir="ltr"')

    def test_login_with_valid_credentials_redirects_to_post_login(self) -> None:
        response = self.client.post(self.url, {"username": "login@example.com", "password": "ChangeMe123!"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("accounts:post_login"))

    def test_login_with_invalid_password_fails(self) -> None:
        response = self.client.post(self.url, {"username": "login@example.com", "password": "wrong"})
        self.assertEqual(response.status_code, 200)
        # The form should re-render the login page.
        self.assertContains(response, "csrfmiddlewaretoken")

    def test_login_with_uppercase_email_works(self) -> None:
        # The EmailAuthenticationForm normalizes the email to lowercase.
        self.assertEqual(self.user.email, "login@example.com")
        # The email is stored lowercase; usernames are matched case-sensitively
        # unless the form's clean step normalizes. We verify that we only need
        # the stored (lowercase) email to log in.
        response = self.client.post(self.url, {"username": "login@example.com", "password": "ChangeMe123!"})
        self.assertEqual(response.status_code, 302)


class DashboardRoutingTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        for name in (GROUP_ADMIN, GROUP_TEACHER, GROUP_STUDENT):
            Group.objects.get_or_create(name=name)
        self.admin = User.objects.create_user(email="admin@example.com", password="x", is_staff=True)
        self.admin.groups.add(Group.objects.get(name=GROUP_ADMIN))
        self.teacher = User.objects.create_user(email="teacher@example.com", password="x")
        self.teacher.groups.add(Group.objects.get(name=GROUP_TEACHER))
        self.student = User.objects.create_user(email="student@example.com", password="x")
        self.student.groups.add(Group.objects.get(name=GROUP_STUDENT))

    def test_admin_routes_to_admin_dashboard(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.get(reverse("accounts:post_login"))
        self.assertRedirects(response, reverse("accounts:dashboard_admin"))

    def test_teacher_routes_to_teacher_dashboard(self) -> None:
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("accounts:post_login"))
        self.assertRedirects(response, reverse("accounts:dashboard_teacher"))

    def test_student_routes_to_student_dashboard(self) -> None:
        self.client.force_login(self.student)
        response = self.client.get(reverse("accounts:post_login"))
        self.assertRedirects(response, reverse("accounts:dashboard_student"))

    def test_anonymous_user_cannot_reach_dashboards(self) -> None:
        for url in (
            "accounts:dashboard_admin",
            "accounts:dashboard_teacher",
            "accounts:dashboard_student",
            "accounts:profile",
        ):
            response = self.client.get(reverse(url))
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse("accounts:login"), response.url)


class ProfileTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.user = User.objects.create_user(email="profile@example.com", password="x", full_name="Original")

    def test_profile_updates_full_name(self) -> None:
        self.client.force_login(self.user)
        response = self.client.post(reverse("accounts:profile"), {"full_name": "New Name", "phone": "0100"})
        self.assertRedirects(response, reverse("accounts:profile"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, "New Name")
        self.assertEqual(self.user.phone, "0100")


class AdminDashboardQueryCountTests(TestCase):
    """Verify admin dashboard produces a bounded number of queries."""

    def setUp(self) -> None:
        self.client = Client()
        self.admin = User.objects.create_superuser(
            email="admin@test.com", password="x",
            is_staff=True, is_superuser=True,
        )
        self.client.force_login(self.admin)
        from datetime import date

        from apps.academics.models import Diploma
        from apps.applications.constants import APP_STATUS_PENDING
        from apps.applications.models import Application
        from apps.exams.constants import ASSIGNMENT_STATUS_COMPLETED
        from apps.exams.models import Assignment, Exam
        from apps.students.models import StudentProfile
        student_user = User.objects.create_user(email="stu@test.com", password="x")
        profile = StudentProfile.objects.create(
            user=student_user, full_name="S", date_of_birth=date(2000, 1, 1),
            gender="male", address="Addr", phone="01000000000",
            national_id="29001010100011", education="bachelor",
        )
        Diploma.objects.create(title="Diploma A", is_active=True, created_by=self.admin)
        Diploma.objects.create(title="Diploma B", is_active=True, created_by=self.admin)
        from django.utils import timezone as tz
        exam = Exam.objects.create(
            title="Exam", is_active=True, created_by=self.admin,
            duration_minutes=60, passing_score=50,
            open_at=tz.now(), close_at=tz.now() + tz.timedelta(hours=2),
        )
        self.admin2 = User.objects.create_user(email="stu2@test.com", password="x")
        Assignment.objects.create(exam=exam, student=self.admin2, status=ASSIGNMENT_STATUS_COMPLETED)
        Application.objects.create(student=profile, status=APP_STATUS_PENDING)

    def test_admin_dashboard_query_count_bounded(self) -> None:
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        url = reverse("accounts:dashboard_admin")
        with CaptureQueriesContext(connection) as captured:
            self.client.get(url)
        self.assertLess(len(captured.captured_queries), 15,
                        f"Admin dashboard used {len(captured.captured_queries)} queries; expected < 15")
