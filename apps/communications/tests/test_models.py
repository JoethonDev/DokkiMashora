"""Tests for the EmailTemplate model and sending service."""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from apps.communications.constants import (
    EMAIL_STATUS_FAILED,
    EMAIL_STATUS_SENT,
    TEMPLATE_RECEIVED,
)
from apps.communications.email_message import EmailMessage
from apps.communications.models import EmailTemplate
from apps.communications.services import render_template, send_email, send_templated_email


class EmailTemplateTests(TestCase):
    def setUp(self) -> None:
        EmailTemplate.objects.create(
            slug=TEMPLATE_RECEIVED,
            name="Registration received",
            subject="Hello {{ full_name }}",
            body="Hi {{ full_name }}, your ID is {{ national_id }}.",
        )

    def test_render_replaces_variables(self) -> None:
        subject, body = render_template(
            TEMPLATE_RECEIVED,
            {"full_name": "Mona", "national_id": "123"},
        )
        self.assertEqual(subject, "Hello Mona")
        self.assertIn("Mona", body)
        self.assertIn("123", body)

    def test_variable_names_lists_placeholders(self) -> None:
        template = EmailTemplate.objects.get(slug=TEMPLATE_RECEIVED)
        names = template.variable_names()
        self.assertEqual(names, ["full_name", "national_id"])

    def test_inactive_template_cannot_be_rendered(self) -> None:
        EmailTemplate.objects.filter(slug=TEMPLATE_RECEIVED).update(is_active=False)
        with self.assertRaises(EmailTemplate.DoesNotExist):
            render_template(TEMPLATE_RECEIVED, {"full_name": "x", "national_id": "y"})


class SendEmailTests(TestCase):
    def setUp(self) -> None:
        EmailTemplate.objects.create(
            slug=TEMPLATE_RECEIVED,
            name="Registration received",
            subject="Hello {{ full_name }}",
            body="Hi {{ full_name }}, your ID is {{ national_id }}.",
        )

    def test_send_email_records_success(self) -> None:
        with self.NoopContext():
            record = send_email(
                template_slug=TEMPLATE_RECEIVED,
                to_email="dest@example.com",
                subject="Hi",
                body="Body",
            )
        self.assertEqual(record.status, EMAIL_STATUS_SENT)
        self.assertEqual(EmailMessage.objects.count(), 1)

    def test_send_templated_email_uses_template(self) -> None:
        with self.NoopContext():
            record = send_templated_email(
                TEMPLATE_RECEIVED,
                "dest@example.com",
                {"full_name": "Mona", "national_id": "1"},
            )
        self.assertEqual(record.status, EMAIL_STATUS_SENT)
        self.assertIn("Mona", record.body)

    def test_send_records_failure(self) -> None:
        with self.NoopContext(), patch("apps.communications.services.DjangoEmailMessage.send") as mocked:
            mocked.side_effect = RuntimeError("smtp down")
            record = send_email(
                template_slug=TEMPLATE_RECEIVED,
                to_email="dest@example.com",
                subject="Hi",
                body="Body",
            )
        self.assertEqual(record.status, EMAIL_STATUS_FAILED)
        self.assertIn("smtp down", record.error)

    class NoopContext:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False
