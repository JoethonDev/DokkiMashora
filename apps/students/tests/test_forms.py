"""Tests for student profile forms."""

from __future__ import annotations

from datetime import date

from django.test import TestCase

from apps.accounts.models import User
from apps.students.constants import EDUCATION_BACHELOR, GENDER_MALE
from apps.students.forms import StudentProfileUpdateForm
from apps.students.models import StudentProfile


class StudentProfileUpdateFormTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(email="student@example.com", password="x")
        self.profile = StudentProfile.objects.create(
            user=self.user,
            full_name="Test Student",
            date_of_birth=date(2000, 1, 1),
            gender=GENDER_MALE,
            address="Cairo",
            phone="01000000000",
            national_id="29001010100011",
            education=EDUCATION_BACHELOR,
        )

    def test_form_fields_are_only_phone_and_address(self) -> None:
        form = StudentProfileUpdateForm(instance=self.profile)
        self.assertEqual(list(form.fields.keys()), ["phone", "address"])

    def test_valid_data_saves(self) -> None:
        form = StudentProfileUpdateForm(
            instance=self.profile,
            data={"phone": "01000000001", "address": "Alexandria"},
        )
        self.assertTrue(form.is_valid())
        form.save()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.phone, "01000000001")
        self.assertEqual(self.profile.address, "Alexandria")

    def test_empty_phone_raises_validation_error(self) -> None:
        form = StudentProfileUpdateForm(
            instance=self.profile,
            data={"phone": "", "address": "Cairo"},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("phone", form.errors)

    def test_phone_uniqueness(self) -> None:
        other_user = User.objects.create_user(email="other@example.com", password="x")
        StudentProfile.objects.create(
            user=other_user,
            full_name="Other",
            date_of_birth=date(2001, 1, 1),
            gender=GENDER_MALE,
            address="Giza",
            phone="01000000002",
            national_id="29001010100022",
            education=EDUCATION_BACHELOR,
        )
        form = StudentProfileUpdateForm(
            instance=self.profile,
            data={"phone": "01000000002", "address": "Cairo"},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("phone", form.errors)

    def test_phone_uniqueness_allows_same_phone_for_same_profile(self) -> None:
        """Editing your own profile with the same phone should be valid."""
        form = StudentProfileUpdateForm(
            instance=self.profile,
            data={"phone": "01000000000", "address": "New Address"},
        )
        self.assertTrue(form.is_valid())

    def test_restricted_fields_in_post_data_are_ignored(self) -> None:
        """Extra fields in POST that are not in EDITABLE_FIELDS are ignored by the form."""
        form = StudentProfileUpdateForm(
            instance=self.profile,
            data={
                "phone": "01000000001",
                "address": "Cairo",
                "full_name": "Should Not Change",
                "national_id": "99999999999999",
            },
        )
        self.assertTrue(form.is_valid())
        form.save()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.full_name, "Test Student")
        self.assertNotEqual(self.profile.national_id, "99999999999999")
