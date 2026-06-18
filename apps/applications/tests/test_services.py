"""Tests for application duplicate matching and registration submission."""

from __future__ import annotations

from datetime import date

from django.test import TestCase

from apps.accounts.models import User
from apps.applications.constants import APP_STATUS_PENDING
from apps.applications.models import Application
from apps.applications.services import (
    RegistrationData,
    find_existing_user,
    submit_registration,
)
from apps.students.constants import EDUCATION_BACHELOR, GENDER_MALE
from apps.students.models import StudentProfile


def make_data(**overrides) -> RegistrationData:
    base = {
        "email": "new@example.com",
        "full_name": "New Applicant",
        "date_of_birth": date(2000, 1, 1),
        "gender": GENDER_MALE,
        "address": "Cairo",
        "phone": "01000000000",
        "national_id": "29001010100011",
        "education": EDUCATION_BACHELOR,
        "specialization": "",
        "employer": "",
        "current_job": "",
        "church": "",
        "priest_name": "",
        "priest_contact": "",
        "age": 25,
    }
    base.update(overrides)
    return RegistrationData(**base)


class FindExistingUserTests(TestCase):
    def setUp(self) -> None:
        self.existing = User.objects.create_user(
            email="known@example.com", password="x", phone="01000000001", national_id="1234567890"
        )
        StudentProfile.objects.create(
            user=self.existing,
            full_name="Known",
            date_of_birth=date(1990, 1, 1),
            gender=GENDER_MALE,
            address="X",
            phone="01000000001",
            national_id="1234567890",
            education=EDUCATION_BACHELOR,
        )

    def test_match_by_email(self) -> None:
        user, field = find_existing_user(email="known@example.com", national_id="0", phone="0")
        self.assertEqual(user, self.existing)
        self.assertEqual(field, "email")

    def test_match_by_national_id(self) -> None:
        user, field = find_existing_user(email="other@example.com", national_id="1234567890", phone="0")
        self.assertEqual(user, self.existing)
        self.assertEqual(field, "national_id")

    def test_match_by_phone(self) -> None:
        user, field = find_existing_user(email="other@example.com", national_id="0", phone="01000000001")
        self.assertEqual(user, self.existing)
        self.assertEqual(field, "phone")

    def test_no_match(self) -> None:
        user, field = find_existing_user(email="other@example.com", national_id="0", phone="0")
        self.assertIsNone(user)
        self.assertIsNone(field)


class SubmitRegistrationTests(TestCase):
    def test_first_submission_creates_user_and_application(self) -> None:
        profile, application, created = submit_registration(make_data())
        self.assertTrue(created)
        self.assertEqual(profile.full_name, "New Applicant")
        self.assertEqual(application.status, APP_STATUS_PENDING)
        self.assertEqual(Application.objects.count(), 1)

    def test_duplicate_email_reuses_user(self) -> None:
        submit_registration(make_data())
        profile, application, created = submit_registration(
            make_data(email="new@example.com", national_id="999", phone="999")
        )
        self.assertFalse(created)
        self.assertEqual(User.objects.filter(email="new@example.com").count(), 1)
        self.assertEqual(Application.objects.count(), 1)

    def test_duplicate_national_id_reuses_profile(self) -> None:
        submit_registration(make_data())
        profile, application, created = submit_registration(
            make_data(email="different@example.com", national_id="29001010100011", phone="999")
        )
        self.assertFalse(created)
        self.assertEqual(profile.user.email, "different@example.com")
        self.assertEqual(StudentProfile.objects.count(), 1)
        self.assertEqual(Application.objects.count(), 1)
