"""Tests for the Application model status transitions."""

from __future__ import annotations

from datetime import date

from django.contrib.auth.models import Group
from django.test import TestCase

from apps.accounts.constants import GROUP_ADMIN
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


class ApplicationTransitionTests(TestCase):
    def setUp(self) -> None:
        self.admin_group, _ = Group.objects.get_or_create(name=GROUP_ADMIN)
        self.reviewer = User.objects.create_user(email="reviewer@example.com", password="x", is_staff=True)
        self.reviewer.groups.add(self.admin_group)
        self.user = User.objects.create_user(email="applicant@example.com", password="x")
        self.profile = StudentProfile.objects.create(
            user=self.user,
            full_name="Applicant",
            date_of_birth=date(2000, 1, 1),
            gender=GENDER_MALE,
            address="Cairo",
            phone="01000000000",
            national_id="29001010100011",
            education=EDUCATION_BACHELOR,
        )
        self.application = Application.objects.create(student=self.profile)

    def test_initial_status_is_pending(self) -> None:
        self.assertEqual(self.application.status, APP_STATUS_PENDING)

    def test_mark_under_review(self) -> None:
        self.application.mark_under_review(self.reviewer)
        self.assertEqual(self.application.status, APP_STATUS_UNDER_REVIEW)
        self.assertEqual(self.application.reviewed_by, self.reviewer)

    def test_approve_records_reviewer_and_timestamp(self) -> None:
        self.application.approve(self.reviewer, notes="Looks good")
        self.assertEqual(self.application.status, APP_STATUS_APPROVED)
        self.assertEqual(self.application.reviewed_by, self.reviewer)
        self.assertIsNotNone(self.application.reviewed_at)
        self.assertEqual(self.application.admin_notes, "Looks good")

    def test_reject_requires_reason_and_stores_it(self) -> None:
        self.application.reject(self.reviewer, reason="Incomplete info")
        self.assertEqual(self.application.status, APP_STATUS_REJECTED)
        self.assertEqual(self.application.rejection_reason, "Incomplete info")
        self.assertEqual(self.application.reviewed_by, self.reviewer)

    def test_activate_sets_timestamp_and_status(self) -> None:
        self.application.approve(self.reviewer)
        self.application.activate()
        self.assertEqual(self.application.status, APP_STATUS_ACTIVATED)
        self.assertIsNotNone(self.application.activated_at)
