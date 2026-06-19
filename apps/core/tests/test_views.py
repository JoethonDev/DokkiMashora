"""Tests for the core app: landing page, error pages, timezone."""

from __future__ import annotations

import re

from django.conf import settings
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone, translation

from apps.accounts.constants import GROUP_STUDENT
from apps.accounts.models import User


class LandingPageTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_landing_page_renders_in_arabic_by_default(self) -> None:
        with translation.override("ar"):
            response = self.client.get(reverse("core:landing"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'lang="ar"')
        self.assertContains(response, 'dir="rtl"')

    def test_landing_page_renders_in_english_when_activated(self) -> None:
        with translation.override("en"):
            response = self.client.get(reverse("core:landing"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'lang="en"')
        self.assertContains(response, 'dir="ltr"')
        self.assertContains(response, "DokkiMashora")

    def test_landing_page_does_not_require_login(self) -> None:
        response = self.client.get(reverse("core:landing"))
        self.assertEqual(response.status_code, 200)

    def test_timezone_is_cairo(self) -> None:
        self.assertEqual(settings.TIME_ZONE, "Africa/Cairo")
        self.assertTrue(settings.USE_TZ)

    def test_default_language_is_arabic(self) -> None:
        self.assertEqual(settings.LANGUAGE_CODE, "ar")
        codes = {code for code, _name in settings.LANGUAGES}
        self.assertIn("ar", codes)
        self.assertIn("en", codes)

    def test_footer_shows_cairo_time(self) -> None:
        response = self.client.get(reverse("core:landing"))
        self.assertEqual(response.status_code, 200)
        # Footer should at least contain the timezone abbreviation (EET/EEST/UTC).
        self.assertTrue(bool(re.search(r"\b(UTC|EET|EEST)\b", response.content.decode("utf-8"))))
        # `now` returned by Django is timezone-aware.
        self.assertIsNotNone(timezone.now().tzinfo)


class ErrorHandlerTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_404_handler_renders(self) -> None:
        response = self.client.get("/this-page-does-not-exist/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "404", status_code=404)


class DesignShowcaseTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.url = reverse("core:design_showcase")

    def test_design_showcase_requires_login(self) -> None:
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_design_showcase_rejects_student(self) -> None:
        student = User.objects.create_user(email="student-showcase@example.com", password="pass")
        group, _created = Group.objects.get_or_create(name=GROUP_STUDENT)
        student.groups.add(group)
        self.client.force_login(student)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_design_showcase_allows_superuser(self) -> None:
        admin = User.objects.create_superuser(email="admin-showcase@example.com", password="pass")
        self.client.force_login(admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Operations Classic")
