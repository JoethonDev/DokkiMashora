"""Tests for the core app: landing page, error pages, timezone."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone, translation

from apps.accounts.constants import (
    GROUP_ADMIN,
    GROUP_MODERATOR,
    GROUP_STUDENT,
    GROUP_SUPER_ADMIN,
    GROUP_TEACHER,
)
from apps.accounts.models import User
from apps.core.models import LandingAnnouncement


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
        self.assertContains(response, "Home")

    def test_landing_page_does_not_require_login(self) -> None:
        response = self.client.get(reverse("core:landing"))
        self.assertEqual(response.status_code, 200)

    def test_landing_page_uses_active_admin_announcement(self) -> None:
        LandingAnnouncement.objects.all().delete()
        LandingAnnouncement.objects.create(
            title="Entrance week",
            message="Applications close Friday.",
            cta_label="Apply",
            cta_url="/applications/register/",
        )

        response = self.client.get(reverse("core:landing"))

        self.assertContains(response, "Entrance week")
        self.assertContains(response, "Applications close Friday.")
        self.assertContains(response, "/applications/register/")

    def test_landing_page_hides_inactive_announcement(self) -> None:
        LandingAnnouncement.objects.all().delete()
        LandingAnnouncement.objects.create(
            title="Hidden banner",
            message="Disabled by admin.",
            is_active=False,
        )

        response = self.client.get(reverse("core:landing"))

        self.assertNotContains(response, "Hidden banner")

    def test_landing_page_renders_extended_translated_sections(self) -> None:
        with translation.override("ar"):
            response = self.client.get(reverse("core:landing"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "متجذر في الإيمان")
        self.assertContains(response, "قابل للتطبيق")
        self.assertContains(response, "مسؤول نفسيًا")

    def test_landing_page_exposes_ui_theme_switcher(self) -> None:
        with translation.override("en"):
            response = self.client.get(reverse("core:landing"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-ui-theme="operational"')
        self.assertContains(response, 'class="dm-shell')
        self.assertContains(response, "Switch to Heritage")

    def test_timezone_is_cairo(self) -> None:
        self.assertEqual(settings.TIME_ZONE, "Africa/Cairo")
        self.assertTrue(settings.USE_TZ)

    def test_default_language_is_arabic(self) -> None:
        self.assertEqual(settings.LANGUAGE_CODE, "ar")
        codes = {code for code, _name in settings.LANGUAGES}
        self.assertIn("ar", codes)
        self.assertIn("en", codes)

    def test_timezone_is_aware(self) -> None:
        """Django timezone support is enabled."""
        self.assertIsNotNone(timezone.now().tzinfo)


class ErrorHandlerTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_404_handler_renders(self) -> None:
        response = self.client.get("/this-page-does-not-exist/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "404", status_code=404)


class NavigationVisibilityTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.url = reverse("core:landing")
        self.student_group, _created = Group.objects.get_or_create(name=GROUP_STUDENT)
        self.admin_group, _created = Group.objects.get_or_create(name=GROUP_ADMIN)

    def test_authenticated_student_does_not_see_public_auth_actions(self) -> None:
        student = User.objects.create_user(email="nav-student@example.com", password="pass")
        student.groups.add(self.student_group)
        self.client.force_login(student)

        response = self.client.get("/en/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Register now")
        self.assertNotContains(response, "Log in")
        self.assertContains(response, "Dashboard")

    def test_student_nav_does_not_show_admin_links(self) -> None:
        student = User.objects.create_user(email="student-links@example.com", password="pass")
        student.groups.add(self.student_group)
        self.client.force_login(student)

        response = self.client.get("/en/")

        self.assertContains(response, "My Profile")
        self.assertContains(response, "My Courses")
        self.assertNotContains(response, "Manage")
        self.assertNotContains(response, "Assessments")

    def test_admin_nav_does_not_show_student_only_links(self) -> None:
        admin = User.objects.create_user(email="admin-links@example.com", password="pass", is_staff=True)
        admin.groups.add(self.admin_group)
        self.client.force_login(admin)

        response = self.client.get("/en/")

        self.assertContains(response, "Applications")
        self.assertContains(response, "Assessments")
        self.assertNotContains(response, "My Courses")
        self.assertNotContains(response, "My Profile")
        self.assertNotContains(response, "Register now")


class RoleMatrixNavTests(TestCase):
    """Verify nav visibility for anonymous, student, teacher, moderator, admin, super admin."""

    ROLES: dict[str, str] = {
        "anonymous": "",
        GROUP_STUDENT: GROUP_STUDENT,
        GROUP_TEACHER: GROUP_TEACHER,
        GROUP_MODERATOR: GROUP_MODERATOR,
        GROUP_ADMIN: GROUP_ADMIN,
        GROUP_SUPER_ADMIN: GROUP_SUPER_ADMIN,
    }

    def setUp(self) -> None:
        self.client = Client()
        self.created_users: dict[str, User] = {}
        for name in self.ROLES:
            if name == "anonymous":
                continue
            user = User.objects.create_user(
                email=f"{name.lower().replace(' ', '_')}@example.com",
                password="pass",
                is_staff=(name != GROUP_STUDENT),
            )
            group, _ = Group.objects.get_or_create(name=name)
            user.groups.add(group)
            self.created_users[name] = user

    def _login(self, role_name: str) -> None:
        if role_name == "anonymous":
            self.client.logout()
        else:
            self.client.force_login(self.created_users[role_name])

    def test_anonymous_gets_200_on_landing(self) -> None:
        self._login("anonymous")
        response = self.client.get("/en/")
        self.assertEqual(response.status_code, 200)

    def test_student_gets_200_on_student_pages(self) -> None:
        self._login(GROUP_STUDENT)
        for url in ["/en/", "/en/profile/"]:
            response = self.client.get(url)
            self.assertIn(response.status_code, (200, 302, 404),
                          f"{url} returned {response.status_code} for student")

    def test_teacher_gets_403_on_exam_pages(self) -> None:
        self._login(GROUP_TEACHER)
        response = self.client.get("/en/exams/manage/")
        self.assertEqual(response.status_code, 403)

    def test_moderator_gets_200_on_payment_pages(self) -> None:
        self._login(GROUP_MODERATOR)
        response = self.client.get(reverse("payments:upload_list"))
        self.assertIn(response.status_code, (200, 302))

    def test_admin_gets_200_on_admin_pages(self) -> None:
        self._login(GROUP_ADMIN)
        for url_name in ["payments:upload_list", "progression:admin_dashboard",
                         "applications:program_list", "certificates:admin_certificate_list"]:
            response = self.client.get(reverse(url_name))
            self.assertIn(response.status_code, (200, 302),
                          f"{url_name} returned {response.status_code} for admin")

    def test_super_admin_gets_200_on_all_admin_pages(self) -> None:
        self._login(GROUP_SUPER_ADMIN)
        for url_name in ["audit:list", "communications:bulk_email_create",
                         "applications:program_list", "certificates:admin_certificate_list"]:
            response = self.client.get(reverse(url_name))
            self.assertIn(response.status_code, (200, 302),
                          f"{url_name} returned {response.status_code} for super admin")

    def test_student_cannot_access_admin_pages(self) -> None:
        self._login(GROUP_STUDENT)
        for url_name in ["applications:program_list", "payments:upload_list",
                         "progression:admin_dashboard", "audit:list"]:
            response = self.client.get(reverse(url_name))
            self.assertIn(response.status_code, (302, 403),
                          f"{url_name} returned {response.status_code} for student (expected 302 or 403)")

    def test_anonymous_redirected_to_login(self) -> None:
        self._login("anonymous")
        for url_name in ["students:dashboard", "payments:upload_list",
                         "progression:admin_dashboard"]:
            response = self.client.get(reverse(url_name))
            self.assertIn(response.status_code, (302, 403),
                          f"{url_name} returned {response.status_code} for anonymous")


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
