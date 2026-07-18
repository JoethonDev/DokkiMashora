"""Small Phase 8 regression checks for operational workflow contracts."""

from pathlib import Path
import unittest

from django import forms
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import GROUP_STUDENT
from apps.accounts.models import User
from apps.core.templatetags.form_tags import add_class

ROOT = Path(__file__).resolve().parent


class Phase8WorkflowTests(unittest.TestCase):
    def test_operational_surfaces_keep_permissions_and_safe_states(self):
        payments = (ROOT / "apps/payments/views.py").read_text(encoding="utf-8")
        audit = (ROOT / "apps/audit/views.py").read_text(encoding="utf-8")
        communications = (ROOT / "apps/communications/views.py").read_text(encoding="utf-8")
        self.assertIn("@role_required(*ROLE_PAYMENT_STAFF)", payments)
        self.assertIn("@require_POST", payments)
        self.assertIn("@role_required(*ROLE_AUDIT_STAFF)", audit)
        self.assertIn("@role_required(*ROLE_SENIOR_STAFF)", communications)
        self.assertIn("The file could not be processed", payments)

    def test_phase8_templates_use_accessible_fallbacks(self):
        composer = (ROOT / "apps/communications/templates/communications/bulk_email_create.html").read_text(encoding="utf-8")
        intake = (ROOT / "apps/intake_forms/templates/intake_forms/fill_form.html").read_text(encoding="utf-8")
        preview = (ROOT / "apps/communications/templates/communications/bulk_email_preview.html").read_text(encoding="utf-8")
        self.assertNotIn("fillTemplate(", composer)
        self.assertIn("x-model=\"subject\"", composer)
        self.assertNotIn("form.as_p", intake)
        self.assertIn("role=\"alert\"", intake)
        self.assertIn("sandbox", preview)


class Phase8RuntimeTests(TestCase):
    def setUp(self):
        self.group, _ = Group.objects.get_or_create(name=GROUP_STUDENT)
        self.user = User.objects.create_user(email="phase8-student@example.com", password="pass")
        self.user.groups.add(self.group)

    def test_student_is_forbidden_from_payment_and_audit_admin_routes(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("payments:upload_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("audit:list")).status_code, 403)

    def test_non_field_error_id_matches_described_by(self):
        class IntakeTestForm(forms.Form):
            answer = forms.CharField()

        form = IntakeTestForm(data={"answer": ""}, prefix="intake")
        form.is_valid()
        form.add_error(None, "Form error")
        rendered = str(add_class(form["answer"], "input"))
        self.assertIn("intake-non-field-errors", rendered)

        unprefixed = IntakeTestForm(data={"answer": ""})
        unprefixed.is_valid()
        unprefixed.add_error(None, "Form error")
        rendered = str(add_class(unprefixed["answer"], "input"))
        self.assertIn("form-non-field-errors", rendered)


if __name__ == "__main__":
    unittest.main()
