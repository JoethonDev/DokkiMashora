"""Tests for student profile views."""

from __future__ import annotations

from datetime import date

from django.test import TestCase
from django.urls import reverse
from django.utils.translation import override as translation_override

from apps.academics.models import Cohort, Diploma, DiplomaEnrollment
from apps.accounts.models import User
from apps.applications.constants import (
    APP_STATUS_ACTIVATED,
    APP_STATUS_PENDING,
    APP_STATUS_REJECTED,
)
from apps.applications.models import Application
from apps.students.constants import EDUCATION_BACHELOR, GENDER_MALE
from apps.students.models import StudentProfile


def _create_student(email="student@example.com") -> tuple[User, StudentProfile]:
    user = User.objects.create_user(email=email, password="x", full_name="Test Student")
    profile = StudentProfile.objects.create(
        user=user,
        full_name="Test Student",
        date_of_birth=date(2000, 1, 1),
        gender=GENDER_MALE,
        address="Cairo",
        phone="01000000000",
        national_id="29001010100011",
        education=EDUCATION_BACHELOR,
    )
    return user, profile


LOGIN_URL = "/login/"


class AnonymousAccessTests(TestCase):
    """All profile views should redirect anonymous users to login."""

    def test_all_views_redirect_anonymous(self) -> None:
        names = [
            "students:dashboard", "students:details", "students:edit_profile",
            "students:application_history", "students:enrollment_history",
            "students:access_list", "students:exam_results",
            "students:assessment_history", "students:homework_list",
            "students:progression_history", "students:certificate_list",
            "students:payment_status", "students:notification_history",
            "students:reapply",
        ]
        for name in names:
            url = reverse(name)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, f"{name} should redirect")
            self.assertIn(LOGIN_URL, response.url, f"{name} should redirect to login")


class AuthenticatedAccessTests(TestCase):
    """Student users should be able to access all profile views."""

    def setUp(self) -> None:
        self.user, self.profile = _create_student()
        self.client.force_login(self.user)

    def test_dashboard_accessible(self) -> None:
        self.assertEqual(self.client.get(reverse("students:dashboard")).status_code, 200)

    def test_details_accessible(self) -> None:
        self.assertEqual(self.client.get(reverse("students:details")).status_code, 200)

    def test_edit_profile_accessible(self) -> None:
        self.assertEqual(self.client.get(reverse("students:edit_profile")).status_code, 200)

    def test_application_history_accessible(self) -> None:
        self.assertEqual(self.client.get(reverse("students:application_history")).status_code, 200)

    def test_enrollment_history_accessible(self) -> None:
        self.assertEqual(self.client.get(reverse("students:enrollment_history")).status_code, 200)

    def test_access_list_accessible(self) -> None:
        self.assertEqual(self.client.get(reverse("students:access_list")).status_code, 200)

    def test_exam_results_accessible(self) -> None:
        self.assertEqual(self.client.get(reverse("students:exam_results")).status_code, 200)

    def test_assessment_history_accessible(self) -> None:
        self.assertEqual(self.client.get(reverse("students:assessment_history")).status_code, 200)

    def test_homework_list_accessible(self) -> None:
        self.assertEqual(self.client.get(reverse("students:homework_list")).status_code, 200)

    def test_progression_history_accessible(self) -> None:
        self.assertEqual(self.client.get(reverse("students:progression_history")).status_code, 200)

    def test_certificate_list_accessible(self) -> None:
        self.assertEqual(self.client.get(reverse("students:certificate_list")).status_code, 200)

    def test_payment_status_accessible(self) -> None:
        self.assertEqual(self.client.get(reverse("students:payment_status")).status_code, 200)

    def test_notification_history_accessible(self) -> None:
        self.assertEqual(self.client.get(reverse("students:notification_history")).status_code, 200)

    def test_reapply_accessible(self) -> None:
        self.assertEqual(self.client.get(reverse("students:reapply")).status_code, 200)


class DashboardViewTests(TestCase):
    def setUp(self) -> None:
        self.user, self.profile = _create_student()
        self.client.force_login(self.user)

    def test_dashboard_renders_welcome(self) -> None:
        with translation_override("en"):
            response = self.client.get(reverse("students:dashboard"))
        self.assertContains(response, "Test Student")

    def test_dashboard_shows_stat_cards(self) -> None:
        with translation_override("en"):
            response = self.client.get(reverse("students:dashboard"))
        self.assertContains(response, "Welcome")

    def test_dashboard_shows_empty_state_when_no_data(self) -> None:
        with translation_override("en"):
            response = self.client.get(reverse("students:dashboard"))
        self.assertContains(response, "No academic records yet")


class DetailsViewTests(TestCase):
    def setUp(self) -> None:
        self.user, self.profile = _create_student()
        self.client.force_login(self.user)

    def test_details_shows_identity_fields(self) -> None:
        with translation_override("en"):
            response = self.client.get(reverse("students:details"))
        self.assertContains(response, "Test Student")
        self.assertContains(response, "student@example.com")
        self.assertContains(response, "Phone number")
        self.assertContains(response, "Edit allowed fields")


class EditProfileViewTests(TestCase):
    def setUp(self) -> None:
        self.user, self.profile = _create_student()
        self.client.force_login(self.user)

    def test_edit_get_renders_form(self) -> None:
        with translation_override("en"):
            response = self.client.get(reverse("students:edit_profile"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "phone")
        self.assertContains(response, "address")

    def test_edit_post_updates_fields(self) -> None:
        with translation_override("en"):
            response = self.client.post(
                reverse("students:edit_profile"),
                {"phone": "01000000001", "address": "Alexandria"},
            )
            self.assertRedirects(response, reverse("students:details"))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.phone, "01000000001")
        self.assertEqual(self.profile.address, "Alexandria")

    def test_edit_post_invalid_shows_errors(self) -> None:
        with translation_override("en"):
            response = self.client.post(
                reverse("students:edit_profile"),
                {"phone": "", "address": ""},
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "required")


class ApplicationHistoryViewTests(TestCase):
    def setUp(self) -> None:
        self.user, self.profile = _create_student()
        self.client.force_login(self.user)

    def test_shows_empty_when_no_applications(self) -> None:
        with translation_override("en"):
            response = self.client.get(reverse("students:application_history"))
        self.assertEqual(response.status_code, 200)

    def test_shows_application_when_exists(self) -> None:
        Application.objects.create(student=self.profile, status=APP_STATUS_PENDING)
        with translation_override("en"):
            response = self.client.get(reverse("students:application_history"))
        self.assertContains(response, "Pending")


class EnrollmentHistoryViewTests(TestCase):
    def setUp(self) -> None:
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.user, self.profile = _create_student()
        self.client.force_login(self.user)
        self.diploma = Diploma.objects.create(title="Test Diploma", created_by=self.admin)
        self.cohort = Cohort.objects.create(
            diploma=self.diploma, name="C2025",
            start_date=date(2025, 9, 1), created_by=self.admin,
        )

    def test_shows_diploma_enrollment(self) -> None:
        DiplomaEnrollment.objects.create(
            student=self.profile, cohort=self.cohort, status="active",
        )
        response = self.client.get(reverse("students:enrollment_history"))
        self.assertEqual(response.status_code, 200)


class ReapplyViewTests(TestCase):
    def setUp(self) -> None:
        self.user, self.profile = _create_student()
        self.client.force_login(self.user)
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.diploma = Diploma.objects.create(title="Test Diploma", created_by=self.admin)
        self.cohort = Cohort.objects.create(
            diploma=self.diploma, name="C2025",
            start_date=date.today(), created_by=self.admin,
        )

    def test_get_shows_eligibility(self) -> None:
        with translation_override("en"):
            response = self.client.get(reverse("students:reapply"))
        self.assertEqual(response.status_code, 200)

    def test_post_when_no_existing_application_creates_new(self) -> None:
        with translation_override("en"):
            response = self.client.post(reverse("students:reapply"), {"cohort": self.cohort.pk})
            self.assertRedirects(response, reverse("students:application_history"))
        apps = list(Application.objects.filter(student=self.profile))
        self.assertEqual(len(apps), 1)
        self.assertEqual(apps[0].status, APP_STATUS_PENDING)
        self.assertEqual(apps[0].cohort, self.cohort)

    def test_post_when_rejected_creates_fresh_record(self) -> None:
        Application.objects.create(student=self.profile, status=APP_STATUS_REJECTED)
        with translation_override("en"):
            response = self.client.post(reverse("students:reapply"), {"cohort": self.cohort.pk})
            self.assertRedirects(response, reverse("students:application_history"))
        apps = list(Application.objects.filter(student=self.profile))
        self.assertEqual(len(apps), 2)
        pending_apps = [a for a in apps if a.status == APP_STATUS_PENDING]
        self.assertEqual(len(pending_apps), 1)

    def test_post_when_pending_blocks_and_shows_error(self) -> None:
        Application.objects.create(student=self.profile, status=APP_STATUS_PENDING)
        with translation_override("en"):
            response = self.client.post(reverse("students:reapply"), {"cohort": self.cohort.pk})
            self.assertRedirects(response, reverse("students:reapply"))

    def test_post_does_not_overwrite_existing_activated(self) -> None:
        existing = Application.objects.create(
            student=self.profile, status=APP_STATUS_ACTIVATED,
        )
        with translation_override("en"):
            response = self.client.post(reverse("students:reapply"), {"cohort": self.cohort.pk})
            self.assertRedirects(response, reverse("students:application_history"))
        existing.refresh_from_db()
        self.assertEqual(existing.status, APP_STATUS_ACTIVATED)

    def test_profile_without_student_profile_returns_404(self) -> None:
        """A user without a StudentProfile should get 404 on profile views."""
        user = User.objects.create_user(email="noprofile@example.com", password="x")
        self.client.force_login(user)
        response = self.client.get(reverse("students:dashboard"))
        self.assertEqual(response.status_code, 404)
