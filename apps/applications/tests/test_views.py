"""Tests for the admin review workflow."""

from __future__ import annotations

from datetime import date

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import GROUP_ADMIN, GROUP_MODERATOR
from apps.accounts.models import User
from apps.applications.constants import (
    APP_STATUS_ACTIVATED,
    APP_STATUS_APPROVED,
    APP_STATUS_PENDING,
    APP_STATUS_REJECTED,
    APP_STATUS_UNDER_REVIEW,
)
from apps.applications.models import Application
from apps.students.constants import EDUCATION_BACHELOR, GENDER_MALE
from apps.students.models import StudentProfile


def make_application(email: str = "applicant@example.com") -> Application:
    user = User.objects.create_user(email=email, password="x")
    profile = StudentProfile.objects.create(
        user=user,
        full_name="Applicant",
        date_of_birth=date(2000, 1, 1),
        gender=GENDER_MALE,
        address="Cairo",
        phone="01000000000",
        national_id="29001010100011",
        education=EDUCATION_BACHELOR,
    )
    return Application.objects.create(student=profile)


class AdminListAccessTests(TestCase):
    def setUp(self) -> None:
        Group.objects.get_or_create(name=GROUP_ADMIN)
        Group.objects.get_or_create(name=GROUP_MODERATOR)
        self.url = reverse("applications:admin_list")

    def test_anonymous_is_redirected_to_login(self) -> None:
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_student_is_forbidden(self) -> None:
        Group.objects.get_or_create(name="Student")
        student = User.objects.create_user(email="s@example.com", password="x")
        student.groups.add(Group.objects.get(name="Student"))
        self.client.force_login(student)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_admin_sees_list(self) -> None:
        admin = User.objects.create_user(email="a@example.com", password="x", is_staff=True)
        admin.groups.add(Group.objects.get(name=GROUP_ADMIN))
        self.client.force_login(admin)
        make_application()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Applicant")

    def test_status_filter(self) -> None:
        admin = User.objects.create_user(email="a@example.com", password="x", is_staff=True)
        admin.groups.add(Group.objects.get(name=GROUP_ADMIN))
        self.client.force_login(admin)
        make_application()
        response = self.client.get(self.url, {"status": APP_STATUS_PENDING})
        self.assertEqual(response.status_code, 200)

    def test_status_filter_hx_returns_table_partial(self) -> None:
        admin = User.objects.create_user(email="a@example.com", password="x", is_staff=True)
        admin.groups.add(Group.objects.get(name=GROUP_ADMIN))
        self.client.force_login(admin)
        make_application()
        response = self.client.get(self.url, {"status": APP_STATUS_PENDING}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Applicant")


class AdminActionTests(TestCase):
    def setUp(self) -> None:
        Group.objects.get_or_create(name=GROUP_ADMIN)
        self.admin = User.objects.create_user(email="admin@example.com", password="x", is_staff=True)
        self.admin.groups.add(Group.objects.get(name=GROUP_ADMIN))
        self.client.force_login(self.admin)
        self.application = make_application()
        self.detail_url = reverse("applications:admin_detail", args=[self.application.id])

    def test_mark_under_review(self) -> None:
        response = self.client.post(self.detail_url, {"action": "mark_review", "admin_notes": ""})
        self.assertEqual(response.status_code, 302)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, APP_STATUS_UNDER_REVIEW)

    def test_approve(self) -> None:
        response = self.client.post(self.detail_url, {"action": "approve", "admin_notes": "ok"})
        self.assertEqual(response.status_code, 302)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, APP_STATUS_APPROVED)

    def test_reject_requires_reason(self) -> None:
        response = self.client.post(self.detail_url, {"action": "reject", "rejection_reason": ""})
        self.assertEqual(response.status_code, 200)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, APP_STATUS_PENDING)

    def test_reject_with_reason(self) -> None:
        response = self.client.post(self.detail_url, {"action": "reject", "rejection_reason": "Incomplete"})
        self.assertEqual(response.status_code, 302)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, APP_STATUS_REJECTED)
        self.assertEqual(self.application.rejection_reason, "Incomplete")

    def test_activate_from_approved(self) -> None:
        self.application.approve(self.admin)
        response = self.client.post(self.detail_url, {"action": "activate"})
        self.assertEqual(response.status_code, 302)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, APP_STATUS_ACTIVATED)
        self.assertTrue(self.application.student.user.groups.filter(name="Student").exists())
