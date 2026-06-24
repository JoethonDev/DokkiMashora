"""Tests for the applications services: duplicate checking, phone normalization, and submission."""

from __future__ import annotations

from datetime import date

from django.contrib.auth.hashers import check_password
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.accounts.constants import GROUP_ADMIN
from apps.accounts.models import User
from apps.applications.constants import (
    APP_STATUS_APPROVED,
    APP_STATUS_ACTIVATED,
    APP_STATUS_PENDING,
    APP_STATUS_REJECTED,
    APP_STATUS_UNDER_REVIEW,
)
from apps.applications.models import Application, ApplicationDecision
from apps.applications.services import (
    RegistrationData,
    activate_application,
    approve_application,
    find_duplicate,
    mark_review_application,
    normalize_phone,
    reject_application,
    revert_application_decision,
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
        profile, application, _ = submit_registration(make_data(phone="01000000000"))
        user = profile.user
        self.assertFalse(user.is_active)
        self.assertTrue(user.groups.filter(name="Student").exists())
        self.assertTrue(check_password("1000000000", user.password))
        self.assertEqual(application.status, APP_STATUS_PENDING)
        self.assertEqual(Application.objects.count(), 1)

    def test_normalizes_phone_before_setting_password(self) -> None:
        profile, _, _ = submit_registration(make_data(phone="+201000000000"))
        self.assertTrue(check_password("1000000000", profile.user.password))

    def test_stores_original_phone_on_profile(self) -> None:
        profile, _, _ = submit_registration(make_data(phone="+201000000000"))
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


class DecisionServiceTests(TestCase):
    def setUp(self) -> None:
        Group.objects.get_or_create(name=GROUP_ADMIN)
        self.admin = User.objects.create_user(email="admin@test.com", password="x", is_staff=True)
        self.admin.groups.add(Group.objects.get(name=GROUP_ADMIN))
        user = User.objects.create_user(email="s@test.com", password="x")
        self.profile = StudentProfile.objects.create(
            user=user,
            full_name="S",
            date_of_birth=date(2000, 1, 1),
            gender=GENDER_MALE,
            address="X",
            phone="01000000000",
            national_id="29001010100011",
            education=EDUCATION_BACHELOR,
        )
        self.application = Application.objects.create(student=self.profile)

    def test_approve_creates_decision_and_audit(self) -> None:
        approve_application(self.application, self.admin, notes="ok")
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, APP_STATUS_APPROVED)
        decision = ApplicationDecision.objects.filter(target_type="application").first()
        self.assertIsNotNone(decision)
        self.assertEqual(decision.action, "approve")
        self.assertEqual(decision.old_status, APP_STATUS_PENDING)
        self.assertEqual(decision.new_status, APP_STATUS_APPROVED)
        self.assertEqual(decision.actor, self.admin)

    def test_reject_creates_decision_and_audit(self) -> None:
        reject_application(self.application, self.admin, reason="incomplete")
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, APP_STATUS_REJECTED)
        self.assertEqual(self.application.rejection_reason, "incomplete")
        decision = ApplicationDecision.objects.filter(target_type="application").first()
        self.assertEqual(decision.action, "reject")

    def test_mark_review_creates_decision(self) -> None:
        mark_review_application(self.application, self.admin)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, APP_STATUS_UNDER_REVIEW)
        self.assertEqual(ApplicationDecision.objects.count(), 1)

    def test_activate_creates_decision_and_activates_user(self) -> None:
        approve_application(self.application, self.admin)
        activate_application(self.application, self.admin)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, APP_STATUS_ACTIVATED)
        self.assertTrue(self.profile.user.groups.filter(name="Student").exists())
        decisions = ApplicationDecision.objects.filter(target_type="application").order_by("-pk")
        self.assertEqual(decisions.count(), 2)
        self.assertEqual(decisions[0].action, "activate")

    def test_revert_approve_restores_previous_status(self) -> None:
        approve_application(self.application, self.admin)
        revert_application_decision(self.application, self.admin, reason="mistake")
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, APP_STATUS_PENDING)
        decisions = ApplicationDecision.objects.filter(target_type="application").order_by("-pk")
        self.assertEqual(decisions.count(), 2)
        last = decisions[0]
        self.assertEqual(last.action, "revert")
        self.assertEqual(last.old_status, APP_STATUS_APPROVED)
        self.assertEqual(last.new_status, APP_STATUS_PENDING)
        self.assertEqual(last.reason, "mistake")

    def test_revert_reject_restores_previous_status(self) -> None:
        reject_application(self.application, self.admin, reason="incomplete")
        revert_application_decision(self.application, self.admin, reason="reconsidered")
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, APP_STATUS_PENDING)

    def test_revert_raises_error_when_no_decision(self) -> None:
        with self.assertRaises(ValueError):
            revert_application_decision(self.application, self.admin, reason="x")

    def test_revert_approve_under_review(self) -> None:
        self.application.mark_under_review(self.admin)
        approve_application(self.application, self.admin)
        revert_application_decision(self.application, self.admin, reason="mistake")
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, APP_STATUS_UNDER_REVIEW)

    def test_revert_not_allowed_activated(self) -> None:
        self.application.approve(self.admin)
        activate_application(self.application, self.admin)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, APP_STATUS_ACTIVATED)
        with self.assertRaises(ValueError):
            revert_application_decision(self.application, self.admin, reason="too late")
