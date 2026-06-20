"""Tests for communications services: resolve_recipients, has_unresolved_variables, send_bulk_email."""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth.models import Group
from django.test import TestCase

from apps.accounts.constants import GROUP_ADMIN
from apps.accounts.models import User

from ..constants import BULK_STATUS_PARTIAL, BULK_STATUS_SENT, EMAIL_STATUS_FAILED, EMAIL_STATUS_SENT
from ..models import BulkEmailSend
from ..services import has_unresolved_variables, resolve_recipients, send_bulk_email


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
    def test_deduplicates_manual_emails(self) -> None:
        result = resolve_recipients(manual_emails="a@b.com\na@b.com\nc@d.com")
        self.assertEqual(result, ["a@b.com", "c@d.com"])

    def test_skips_invalid_manual_emails(self) -> None:
        result = resolve_recipients(manual_emails="valid@example.com\nnot-an-email\n  \n@missing.com")
        self.assertEqual(result, ["valid@example.com"])

    def test_returns_empty_list_for_no_criteria(self) -> None:
        self.assertEqual(resolve_recipients(), [])

    def test_deduplication_preserves_order(self) -> None:
        result = resolve_recipients(manual_emails="z@z.com\na@a.com\nz@z.com")
        self.assertEqual(result, ["z@z.com", "a@a.com"])

    def test_case_insensitive_dedup(self) -> None:
        result = resolve_recipients(manual_emails="A@B.COM\na@b.com")
        self.assertEqual(result, ["A@B.COM"])


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
        from ..email_message import EmailMessage

        ok_record = EmailMessage(status=EMAIL_STATUS_SENT)
        fail_record = EmailMessage(status=EMAIL_STATUS_FAILED)
        mock_send.side_effect = [ok_record, fail_record]

        result = send_bulk_email(self.bulk)
        self.assertEqual(result.status, BULK_STATUS_PARTIAL)
        self.assertEqual(result.success_count, 1)
        self.assertEqual(result.failure_count, 1)

    @patch("apps.communications.services.send_email")
    def test_send_bulk_reports_success_when_all_succeed(self, mock_send) -> None:
        from ..email_message import EmailMessage

        ok_record = EmailMessage(status=EMAIL_STATUS_SENT)
        mock_send.return_value = ok_record

        self.bulk.recipient_emails = "a@a.com\nb@b.com"
        self.bulk.save(update_fields=["recipient_emails"])

        result = send_bulk_email(self.bulk)
        self.assertEqual(result.status, BULK_STATUS_SENT)
        self.assertEqual(result.success_count, 2)
        self.assertEqual(result.failure_count, 0)
