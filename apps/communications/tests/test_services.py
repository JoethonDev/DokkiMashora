"""Tests for communications services: resolve_recipients, has_unresolved_variables, send_bulk_email."""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth.models import Group
from django.template import TemplateDoesNotExist
from django.test import TestCase

from apps.accounts.constants import GROUP_ADMIN
from apps.accounts.models import User

from ..constants import BULK_STATUS_PARTIAL, BULK_STATUS_SENT, EMAIL_STATUS_FAILED, EMAIL_STATUS_SENT
from ..models import BulkEmailSend
from ..services import has_unresolved_variables, resolve_recipients, send_bulk_email, send_templated_email
from ..email_message import EmailMessage
from ..models import EmailTemplate
from django.template.loader import render_to_string


class HasUnresolvedVariablesTests(TestCase):
    def test_returns_false_for_clean_text(self) -> None:
        self.assertFalse(has_unresolved_variables("Hello Student"))

    def test_returns_true_for_unresolved_variable(self) -> None:
        self.assertTrue(has_unresolved_variables("Hello {{ full_name }}"))

    def test_returns_true_for_unresolved_variable_no_spaces(self) -> None:
        self.assertTrue(has_unresolved_variables("Hi {{name}}"))

    def test_false_for_braces_without_variable_brackets(self) -> None:
        self.assertFalse(has_unresolved_variables("Some {text} here"))


class ResolveRecipientsTests(TestCase):
    @staticmethod
    def _emails(result):
        return [r["email"] for r in result]

    def test_deduplicates_manual_emails(self) -> None:
        result = resolve_recipients(manual_emails="a@b.com\na@b.com\nc@d.com")
        self.assertEqual(self._emails(result), ["a@b.com", "c@d.com"])

    def test_skips_invalid_manual_emails(self) -> None:
        result = resolve_recipients(manual_emails="valid@example.com\nnot-an-email\n  \n@missing.com")
        self.assertEqual(self._emails(result), ["valid@example.com"])

    def test_returns_empty_list_for_no_criteria(self) -> None:
        self.assertEqual(resolve_recipients(), [])

    def test_deduplication_preserves_order(self) -> None:
        result = resolve_recipients(manual_emails="z@z.com\na@a.com\nz@z.com")
        self.assertEqual(self._emails(result), ["z@z.com", "a@a.com"])

    def test_case_insensitive_dedup(self) -> None:
        result = resolve_recipients(manual_emails="A@B.COM\na@b.com")
        self.assertEqual(self._emails(result), ["A@B.COM"])


class SendBulkEmailTests(TestCase):
    def setUp(self) -> None:
        self.admin_group, _ = Group.objects.get_or_create(name=GROUP_ADMIN)
        self.admin = User.objects.create_user(
            email="admin-bulk@example.com", password="pass", is_staff=True
        )
        self.admin.groups.add(self.admin_group)
        self.bulk = BulkEmailSend.objects.create(
            subject="Test Subject",
            body="Test Body",
            recipient_emails="ok@example.com\nfail@example.com",
            recipient_count=2,
            sent_by=self.admin,
        )

    @patch("apps.communications.services.send_email")
    def test_send_bulk_reports_partial_on_failure(self, mock_send) -> None:

        ok_record = EmailMessage(status=EMAIL_STATUS_SENT)
        fail_record = EmailMessage(status=EMAIL_STATUS_FAILED)
        mock_send.side_effect = [ok_record, fail_record]

        result = send_bulk_email(self.bulk)
        self.assertEqual(result.status, BULK_STATUS_PARTIAL)
        self.assertEqual(result.success_count, 1)
        self.assertEqual(result.failure_count, 1)

    @patch("apps.communications.services.send_email")
    def test_send_bulk_reports_success_when_all_succeed(self, mock_send) -> None:

        ok_record = EmailMessage(status=EMAIL_STATUS_SENT)
        mock_send.return_value = ok_record

        self.bulk.recipient_emails = "a@a.com\nb@b.com"
        self.bulk.save(update_fields=["recipient_emails"])

        result = send_bulk_email(self.bulk)
        self.assertEqual(result.status, BULK_STATUS_SENT)
        self.assertEqual(result.success_count, 2)
        self.assertEqual(result.failure_count, 0)


class SendTemplatedEmailDesignTests(TestCase):
    """send_templated_email wraps admin body in action-specific design."""

    def setUp(self) -> None:

        self.admin_user = User.objects.create_user(
            email="design-admin@test.com", password="pass", is_staff=True,
        )
        self.template = EmailTemplate.objects.create(
            slug="test_action",
            subject="Test {{ full_name }}",
            body="<p>Admin body for {{ full_name }}</p>",
            is_active=True,
            is_enabled_for_action=True,
        )

    @patch("apps.communications.services.send_email")
    @patch("apps.communications.services.render_to_string")
    def test_design_wraps_admin_body(self, mock_render, mock_send):
        """When a design template exists, body is passed as 'content' variable."""
        def _mock_render(name, ctx=None):
            if "email_designs/test_action" in name:
                return f"<html>Design: {ctx['content']}</html>"
            return f"<html>Layout: {ctx['content']}</html>"
        mock_render.side_effect = _mock_render
        mock_send.return_value = None

        send_templated_email(
            "test_action",
            "student@test.com",
            {"full_name": "Test Student"},
            sent_by=self.admin_user,
        )

        self.assertTrue(
            any("email_designs/test_action" in str(c) for c in mock_render.call_args_list),
            "render_to_string should be called with email_designs/test_action.html",
        )
        # Final body should contain the admin body wrapped in the action design
        call_str = str(mock_send.call_args)
        self.assertIn("Admin body", call_str)
        self.assertIn("<html>Design:", call_str)

    @patch("apps.communications.services.send_email")
    @patch("apps.communications.services.render_to_string")
    def test_no_design_does_not_wrap(self, mock_render, mock_send):
        """When no design template exists, body is passed through unchanged."""
        call_count = [0]
        def _mock_render(name, ctx=None):
            call_count[0] += 1
            if call_count[0] == 1 and "email_designs/test_action" in name:
                raise TemplateDoesNotExist(name)
            return "<html>Layout</html>"
        mock_render.side_effect = _mock_render
        mock_send.return_value = None

        send_templated_email(
            "test_action",
            "student@test.com",
            {"full_name": "Test Student"},
            sent_by=self.admin_user,
        )

        self.assertIn("Admin body", str(mock_send.call_args))

    def test_disabled_template_returns_none(self):
        """Inactive or disabled templates make send_templated_email return None."""
        EmailTemplate.objects.create(
            slug="disabled_test",
            subject="Disabled {{ full_name }}",
            body="Body",
            is_active=False,
            is_enabled_for_action=True,
        )
        result = send_templated_email(
            "disabled_test",
            "student@test.com",
            {"full_name": "Test"},
            sent_by=self.admin_user,
        )
        self.assertIsNone(result)

    def test_missing_template_returns_none(self):
        """Non-existent template slug makes send_templated_email return None."""
        result = send_templated_email(
            "nonexistent_slug",
            "student@test.com",
            {"full_name": "Test"},
            sent_by=self.admin_user,
        )
        self.assertIsNone(result)


class EmailDesignRenderTests(TestCase):
    """All 5 layout designs and 3 action-specific designs render without error."""

    LAYOUT_DESIGNS = ["branded", "minimal", "modern", "elegant", "clean"]
    ACTION_DESIGNS = ["acceptance", "rejection", "payment_notification"]

    def test_all_layout_designs_render(self) -> None:

        for slug in self.LAYOUT_DESIGNS:
            html = render_to_string(f"communications/email_designs/{slug}.html", {"content": "<p>Test</p>"})
            self.assertIn("Test", html)

    def test_all_action_designs_render(self) -> None:

        for slug in self.ACTION_DESIGNS:
            html = render_to_string(f"communications/email_designs/{slug}.html", {"content": "<p>Test</p>"})
            self.assertIn("Test", html)
