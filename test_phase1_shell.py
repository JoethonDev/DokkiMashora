from pathlib import Path

from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from apps.accounts.models import User


class Phase1ShellTests(TestCase):
    def test_shell_uses_compiled_css_without_runtime_tailwind(self):
        response = self.client.get(reverse("core:landing"))

        self.assertContains(response, "/static/css/tailwind.css")
        self.assertNotContains(response, "cdn.tailwindcss.com")

    def test_authenticated_footer_does_not_offer_login(self):
        user = User.objects.create_user("shell@phase1.test", password="pass")
        self.client.force_login(user)

        response = self.client.get(reverse("core:landing"))

        footer = response.content.decode().split("<footer", 1)[1].split("</footer>", 1)[0]
        self.assertNotIn("accounts/login", footer)

    def test_shell_has_scoped_mobile_menu_and_no_dark_mode_control(self):
        response = self.client.get(reverse("core:landing"))
        html = response.content.decode()

        self.assertIn('x-data class="lg:hidden', html)
        self.assertIn('class="fixed inset-0 z-50 lg:hidden"', html)
        self.assertNotIn("_theme_toggle", html)
        self.assertNotIn("dm-theme", html)

    def test_shell_keeps_motion_accessible(self):
        response = self.client.get(reverse("core:landing"))
        html = response.content.decode()
        self.assertIn("x-transition.opacity", html)
        self.assertIn("prefers-reduced-motion: reduce", (Path(__file__).parent / "static/css/app.css").read_text())

    def test_login_non_field_error_renders_once(self):
        with translation.override("en"):
            response = self.client.post(
                reverse("accounts:login"),
                {"username": "wrong@example.com", "password": "wrong"},
            )

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertEqual(html.count('id="form-non-field-errors"'), 1)
