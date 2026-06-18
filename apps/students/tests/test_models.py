"""Tests for the student profile model."""

from __future__ import annotations

from datetime import date

from django.test import TestCase

from apps.accounts.models import User
from apps.students.constants import EDUCATION_BACHELOR, GENDER_MALE
from apps.students.models import StudentProfile


class StudentProfileTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(
            email="student@example.com",
            password="x",
            full_name="Test Student",
        )
        self.profile = StudentProfile.objects.create(
            user=self.user,
            full_name="Test Student",
            date_of_birth=date(2000, 1, 1),
            gender=GENDER_MALE,
            address="Cairo, Egypt",
            phone="01000000000",
            national_id="29001010100011",
            education=EDUCATION_BACHELOR,
        )

    def test_age_is_calculated_from_date_of_birth(self) -> None:
        today = date.today()
        expected = today.year - 2000 - ((today.month, today.day) < (1, 1))
        self.assertEqual(self.profile.age, expected)

    def test_str_includes_full_name_and_email(self) -> None:
        self.assertIn("Test Student", str(self.profile))
        self.assertIn("student@example.com", str(self.profile))
