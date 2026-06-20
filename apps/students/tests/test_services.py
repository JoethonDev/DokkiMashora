"""Tests for student profile services."""

from __future__ import annotations

from datetime import date

from django.test import TestCase

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
from apps.students.services import (
    check_reapply_eligibility,
    submit_reapply_application,
    update_profile,
)


def _create_profile(email="student@example.com") -> StudentProfile:
    user = User.objects.create_user(email=email, password="x")
    return StudentProfile.objects.create(
        user=user,
        full_name="Test Student",
        date_of_birth=date(2000, 1, 1),
        gender=GENDER_MALE,
        address="Cairo",
        phone="01000000000",
        national_id="29001010100011",
        education=EDUCATION_BACHELOR,
    )


class UpdateProfileTests(TestCase):
    def setUp(self) -> None:
        self.profile = _create_profile()

    def test_updates_allowed_fields(self) -> None:
        updated = update_profile(
            self.profile,
            {"phone": "01000000001", "address": "Alexandria"},
        )
        updated.refresh_from_db()
        self.assertEqual(updated.phone, "01000000001")
        self.assertEqual(updated.address, "Alexandria")

    def test_ignores_restricted_fields(self) -> None:
        update_profile(
            self.profile,
            {"full_name": "Hacker", "national_id": "00000000000000"},
        )
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.full_name, "Test Student")
        self.assertEqual(self.profile.national_id, "29001010100011")

    def test_partial_update_only_touches_provided_fields(self) -> None:
        old_address = self.profile.address
        update_profile(self.profile, {"phone": "01000000002"})
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.phone, "01000000002")
        self.assertEqual(self.profile.address, old_address)


class CheckReapplyEligibilityTests(TestCase):
    def setUp(self) -> None:
        self.profile = _create_profile()

    def test_allowed_when_no_existing_application(self) -> None:
        result = check_reapply_eligibility(self.profile)
        self.assertTrue(result["allowed"])
        self.assertIsNone(result["reason"])

    def test_blocked_when_pending(self) -> None:
        Application.objects.create(student=self.profile, status=APP_STATUS_PENDING)
        result = check_reapply_eligibility(self.profile)
        self.assertFalse(result["allowed"])
        self.assertIsNotNone(result["reason"])

    def test_blocked_when_under_review(self) -> None:
        Application.objects.create(student=self.profile, status=APP_STATUS_UNDER_REVIEW)
        result = check_reapply_eligibility(self.profile)
        self.assertFalse(result["allowed"])
        self.assertIsNotNone(result["reason"])

    def test_blocked_when_any_application_is_pending(self) -> None:
        """A rejected app + a pending app should still block."""
        Application.objects.create(student=self.profile, status=APP_STATUS_REJECTED)
        Application.objects.create(student=self.profile, status=APP_STATUS_PENDING)
        result = check_reapply_eligibility(self.profile)
        self.assertFalse(result["allowed"])

    def test_allowed_when_activated(self) -> None:
        Application.objects.create(student=self.profile, status=APP_STATUS_ACTIVATED)
        result = check_reapply_eligibility(self.profile)
        self.assertTrue(result["allowed"])

    def test_allowed_when_rejected(self) -> None:
        Application.objects.create(student=self.profile, status=APP_STATUS_REJECTED)
        result = check_reapply_eligibility(self.profile)
        self.assertTrue(result["allowed"])

    def test_allowed_when_approved(self) -> None:
        Application.objects.create(student=self.profile, status=APP_STATUS_APPROVED)
        result = check_reapply_eligibility(self.profile)
        self.assertTrue(result["allowed"])


class SubmitReapplyApplicationTests(TestCase):
    def setUp(self) -> None:
        self.profile = _create_profile()

    def test_creates_new_application_when_no_existing(self) -> None:
        app = submit_reapply_application(self.profile)
        self.assertEqual(app.student, self.profile)
        self.assertEqual(app.status, APP_STATUS_PENDING)
        self.assertIsNotNone(app.pk)

    def test_creates_new_application_even_when_rejected_exists(self) -> None:
        existing = Application.objects.create(student=self.profile, status=APP_STATUS_REJECTED)
        app = submit_reapply_application(self.profile)
        self.assertNotEqual(app.pk, existing.pk)
        self.assertEqual(Application.objects.filter(student=self.profile).count(), 2)

    def test_creates_new_application_even_when_activated_exists(self) -> None:
        existing = Application.objects.create(student=self.profile, status=APP_STATUS_ACTIVATED)
        app = submit_reapply_application(self.profile)
        self.assertNotEqual(app.pk, existing.pk)
        self.assertEqual(Application.objects.filter(student=self.profile).count(), 2)

    def test_raises_value_error_when_pending_exists(self) -> None:
        Application.objects.create(student=self.profile, status=APP_STATUS_PENDING)
        with self.assertRaises(ValueError):
            submit_reapply_application(self.profile)

    def test_raises_value_error_when_under_review_exists(self) -> None:
        Application.objects.create(student=self.profile, status=APP_STATUS_UNDER_REVIEW)
        with self.assertRaises(ValueError):
            submit_reapply_application(self.profile)

    def test_does_not_overwrite_existing_record(self) -> None:
        existing = Application.objects.create(student=self.profile, status=APP_STATUS_REJECTED)
        created_at_before = existing.created_at
        submit_reapply_application(self.profile)
        existing.refresh_from_db()
        self.assertEqual(existing.created_at, created_at_before)
        self.assertEqual(existing.status, APP_STATUS_REJECTED)
