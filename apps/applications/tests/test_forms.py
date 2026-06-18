"""Tests for the public registration form."""

from __future__ import annotations

from datetime import date

from django.test import TestCase
from django.urls import reverse

from apps.applications.forms import RegistrationForm
from apps.applications.models import Application


class RegistrationFormTests(TestCase):
    def _data(self, **overrides) -> dict:
        base = {
            "full_name": "New Applicant",
            "date_of_birth": "2000-01-01",
            "gender": "male",
            "address": "Cairo",
            "phone": "01000000000",
            "email": "new@example.com",
            "education": "bachelor",
            "specialization": "",
            "employer": "",
            "current_job": "",
            "church": "",
            "priest_name": "",
            "priest_contact": "",
            "national_id": "29001010100011",
        }
        base.update(overrides)
        return base

    def test_form_valid_with_required_fields(self) -> None:
        form = RegistrationForm(data=self._data())
        self.assertTrue(form.is_valid(), form.errors)

    def test_form_rejects_future_date_of_birth(self) -> None:
        future = date.today().replace(year=date.today().year + 1).isoformat()
        form = RegistrationForm(data=self._data(date_of_birth=future))
        self.assertFalse(form.is_valid())
        self.assertIn("date_of_birth", form.errors)

    def test_form_rejects_missing_email(self) -> None:
        form = RegistrationForm(data=self._data(email=""))
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_form_rejects_missing_national_id(self) -> None:
        form = RegistrationForm(data=self._data(national_id=""))
        self.assertFalse(form.is_valid())
        self.assertIn("national_id", form.errors)

    def test_form_rejects_short_phone(self) -> None:
        form = RegistrationForm(data=self._data(phone="12345"))
        self.assertFalse(form.is_valid())
        self.assertIn("phone", form.errors)

    def test_form_rejects_duplicate_email(self) -> None:
        self.client.post(self._post_url(), self._data())
        form = RegistrationForm(
            data=self._data(phone="01111111111", national_id="999", full_name="Different")
        )
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_form_rejects_duplicate_phone(self) -> None:
        self.client.post(self._post_url(), self._data())
        form = RegistrationForm(
            data=self._data(email="other@example.com", national_id="999", full_name="Different")
        )
        self.assertFalse(form.is_valid())
        self.assertIn("phone", form.errors)

    def test_form_rejects_duplicate_national_id(self) -> None:
        self.client.post(self._post_url(), self._data())
        form = RegistrationForm(
            data=self._data(email="other@example.com", phone="01111111111", full_name="Different")
        )
        self.assertFalse(form.is_valid())
        self.assertIn("national_id", form.errors)

    def test_form_rejects_duplicate_full_name(self) -> None:
        self.client.post(self._post_url(), self._data())
        form = RegistrationForm(
            data=self._data(email="other@example.com", phone="01111111111", national_id="999")
        )
        self.assertFalse(form.is_valid())
        self.assertIn("full_name", form.errors)

    @staticmethod
    def _post_url() -> str:
        return reverse("applications:register")


class RegistrationViewTests(TestCase):
    def setUp(self) -> None:
        self.url = reverse("applications:register")

    def _post(self, **overrides):
        data = {
            "full_name": "New Applicant",
            "date_of_birth": "2000-01-01",
            "gender": "male",
            "address": "Cairo",
            "phone": "01000000000",
            "email": "new@example.com",
            "education": "bachelor",
            "specialization": "",
            "employer": "",
            "current_job": "",
            "church": "",
            "priest_name": "",
            "priest_contact": "",
            "national_id": "29001010100011",
        }
        data.update(overrides)
        return self.client.post(self.url, data)

    def test_get_renders_form(self) -> None:
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="full_name"')

    def test_valid_post_creates_application_and_redirects(self) -> None:
        response = self._post()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("applications:registration_success"))
        self.assertEqual(Application.objects.count(), 1)

    def test_invalid_post_rerenders_form(self) -> None:
        response = self._post(email="")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="email"')
        self.assertEqual(Application.objects.count(), 0)

    def test_authenticated_user_is_redirected_to_dashboard(self) -> None:
        from apps.accounts.models import User

        user = User.objects.create_user(email="logged@example.com", password="x")
        self.client.force_login(user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("accounts:post_login"))

    def test_authenticated_user_cannot_post_registration(self) -> None:
        from apps.accounts.models import User

        user = User.objects.create_user(email="logged@example.com", password="x")
        self.client.force_login(user)
        response = self._post()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Application.objects.count(), 0)
