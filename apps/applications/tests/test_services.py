"""Tests for the applications services: duplicate checking, phone normalization, and submission."""

from __future__ import annotations

from datetime import date

from django.contrib.auth.hashers import check_password
from django.test import TestCase

from apps.accounts.models import User
from apps.applications.constants import APP_STATUS_PENDING
from apps.applications.models import Application
from apps.applications.services import (
    RegistrationData,
    find_duplicate,
    normalize_phone,
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


class NormalizePhoneTests(TestCase):
    def test_strips_egypt_country_code(self) -> None:
        self.assertEqual(normalize_phone("+201234567890"), "1234567890")

    def test_strips_double_zero_international_prefix(self) -> None:
        self.assertEqual(normalize_phone("00201234567890"), "1234567890")

    def test_strips_leading_zero(self) -> None:
        self.assertEqual(normalize_phone("01001234567"), "1001234567")

    def test_removes_spaces_and_dashes(self) -> None:
        self.assertEqual(normalize_phone("+20 100-123-4567"), "1001234567")

    def test_strips_saudi_country_code(self) -> None:
        self.assertEqual(normalize_phone("+966501234567"), "501234567")

    def test_returns_empty_for_no_digits(self) -> None:
        self.assertEqual(normalize_phone(""), "")
        self.assertEqual(normalize_phone("abc"), "")


class FindDuplicateTests(TestCase):
    def setUp(self) -> None:
        user = User.objects.create_user(
            email="existing@example.com", password="x", phone="01000000001",
            national_id="1234567890",
        )
        StudentProfile.objects.create(
            user=user,
            full_name="Existing Person",
            date_of_birth=date(1990, 1, 1),
            gender=GENDER_MALE,
            address="X",
            phone="01000000001",
            national_id="1234567890",
            education=EDUCATION_BACHELOR,
        )

    def test_detects_duplicate_email(self) -> None:
        self.assertEqual(find_duplicate(make_data(email="existing@example.com")), "email")

    def test_detects_duplicate_national_id(self) -> None:
        self.assertEqual(find_duplicate(make_data(national_id="1234567890")), "national_id")

    def test_detects_duplicate_phone(self) -> None:
        self.assertEqual(find_duplicate(make_data(phone="01000000001")), "phone")

    def test_no_duplicate_returns_none(self) -> None:
        self.assertIsNone(find_duplicate(make_data()))


class SubmitRegistrationTests(TestCase):
    def test_first_submission_creates_inactive_user_with_phone_password(self) -> None:
        profile, application = submit_registration(make_data(phone="01000000000"))
        user = profile.user
        self.assertFalse(user.is_active)
        self.assertTrue(user.groups.filter(name="Student").exists())
        self.assertTrue(check_password("1000000000", user.password))
        self.assertEqual(application.status, APP_STATUS_PENDING)
        self.assertEqual(Application.objects.count(), 1)

    def test_normalizes_phone_before_setting_password(self) -> None:
        profile, _ = submit_registration(make_data(phone="+201000000000"))
        self.assertTrue(check_password("1000000000", profile.user.password))

    def test_stores_original_phone_on_profile(self) -> None:
        profile, _ = submit_registration(make_data(phone="+201000000000"))
        self.assertEqual(profile.phone, "+201000000000")

    def test_duplicate_email_raises_value_error(self) -> None:
        submit_registration(make_data())
        with self.assertRaises(ValueError) as ctx:
            submit_registration(
                make_data(email="new@example.com", national_id="999", phone="999", full_name="Different")
            )
        self.assertEqual(str(ctx.exception), "email")

    def test_duplicate_national_id_raises_value_error(self) -> None:
        submit_registration(make_data())
        with self.assertRaises(ValueError) as ctx:
            submit_registration(
                make_data(
                    email="other@example.com",
                    national_id="29001010100011",
                    phone="999",
                    full_name="Different",
                )
            )
        self.assertEqual(str(ctx.exception), "national_id")

    def test_duplicate_phone_raises_value_error(self) -> None:
        submit_registration(make_data())
        with self.assertRaises(ValueError) as ctx:
            submit_registration(
                make_data(
                    email="other@example.com", national_id="999", phone="01000000000", full_name="Different"
                )
            )
        self.assertEqual(str(ctx.exception), "phone")

    def test_duplicate_does_not_create_new_records(self) -> None:
        submit_registration(make_data())
        with self.assertRaises(ValueError):
            submit_registration(
                make_data(
                    email="new@example.com", national_id="999999", phone="01111111111", full_name="Different"
                )
            )
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(StudentProfile.objects.count(), 1)
        self.assertEqual(Application.objects.count(), 1)
