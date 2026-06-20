"""Tests for student profile policies."""

from __future__ import annotations

from datetime import date

from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.students.constants import EDUCATION_BACHELOR, GENDER_MALE
from apps.students.models import StudentProfile
from apps.students.policies import (
    EDITABLE_FIELDS,
    RESTRICTED_FIELDS,
    can_edit_field,
    get_editable_fields,
    get_field_label,
    get_restricted_fields,
    is_own_profile,
)


class EditableFieldsTests(TestCase):
    def test_get_editable_fields_returns_phone_and_address(self) -> None:
        self.assertEqual(get_editable_fields(), ["phone", "address"])

    def test_get_restricted_fields_includes_key_identity_fields(self) -> None:
        fields = get_restricted_fields()
        for f in ("full_name", "national_id", "email", "date_of_birth", "gender"):
            self.assertIn(f, fields)

    def test_editable_fields_constant_matches_function(self) -> None:
        self.assertEqual(get_editable_fields(), EDITABLE_FIELDS)

    def test_restricted_fields_constant_matches_function(self) -> None:
        self.assertEqual(get_restricted_fields(), RESTRICTED_FIELDS)


class CanEditFieldTests(TestCase):
    def test_phone_is_editable(self) -> None:
        self.assertTrue(can_edit_field("phone"))

    def test_address_is_editable(self) -> None:
        self.assertTrue(can_edit_field("address"))

    def test_full_name_is_not_editable(self) -> None:
        self.assertFalse(can_edit_field("full_name"))

    def test_national_id_is_not_editable(self) -> None:
        self.assertFalse(can_edit_field("national_id"))

    def test_unknown_field_is_not_editable(self) -> None:
        self.assertFalse(can_edit_field("nonexistent"))


class GetFieldLabelTests(TestCase):
    def test_returns_translated_label_for_known_field(self) -> None:
        with override_settings(LANGUAGE_CODE="en"):
            self.assertEqual(get_field_label("full_name"), "Full name")
            self.assertEqual(get_field_label("phone"), "Phone number")
            self.assertEqual(get_field_label("address"), "Address")

    def test_returns_field_name_for_unknown_field(self) -> None:
        self.assertEqual(get_field_label("nonexistent"), "nonexistent")


class IsOwnProfileTests(TestCase):
    def setUp(self) -> None:
        self.owner = User.objects.create_user(email="owner@example.com", password="x")
        self.other = User.objects.create_user(email="other@example.com", password="x")
        self.profile = StudentProfile.objects.create(
            user=self.owner,
            full_name="Owner",
            date_of_birth=date(2000, 1, 1),
            gender=GENDER_MALE,
            address="Cairo",
            phone="01000000000",
            national_id="29001010100011",
            education=EDUCATION_BACHELOR,
        )

    def test_owner_owns_profile(self) -> None:
        self.assertTrue(is_own_profile(self.profile, self.owner))

    def test_other_user_does_not_own_profile(self) -> None:
        self.assertFalse(is_own_profile(self.profile, self.other))
