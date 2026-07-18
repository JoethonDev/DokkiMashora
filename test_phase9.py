"""Phase 9 release-hardening smoke checks."""

from pathlib import Path
import unittest

from django.test import TestCase
from django.urls import reverse


ROOT = Path(__file__).resolve().parent


class Phase9ShellChecks(unittest.TestCase):
    def test_keyboard_focus_and_rtl_logical_utilities_are_present(self):
        css = (ROOT / "static/css/app.css").read_text(encoding="utf-8")
        settings = (ROOT / "templates/core/admin_landing_settings.html").read_text(encoding="utf-8")
        self.assertIn(":focus-visible", css)
        self.assertIn(":not(:disabled):focus-visible", css)
        self.assertIn("outline: 2px solid var(--dm-brand)", css)
        self.assertIn("text-end", settings)
        self.assertNotIn("text-right", settings)


class Phase9RuntimeChecks(TestCase):
    def test_rtl_shell_renders_with_release_css(self):
        response = self.client.get(reverse("core:landing"))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('dir="rtl"', html)
        self.assertIn("/static/css/app.css", html)


if __name__ == "__main__":
    unittest.main()
