"""Tests for audit selectors and event creation."""

from __future__ import annotations

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import GROUP_ADMIN, GROUP_SUPER_ADMIN
from apps.accounts.models import User

from ..selectors import get_audit_events
from ..services import record_audit_event


class AuditEventTests(TestCase):
    def setUp(self) -> None:
        self.admin_group, _ = Group.objects.get_or_create(name=GROUP_ADMIN)
        self.super_group, _ = Group.objects.get_or_create(name=GROUP_SUPER_ADMIN)
        self.admin = User.objects.create_user(
            email="audit-admin@example.com", password="pass", is_staff=True
        )
        self.admin.groups.add(self.admin_group)

    def test_record_and_retrieve_event(self) -> None:
        record_audit_event(
            actor=self.admin,
            action="payment.upload_confirmed",
            target_type="PaymentUpload",
            target_id="42",
            metadata={"rows": 10},
        )
        events = get_audit_events()
        self.assertEqual(events.count(), 1)
        event = events.first()
        self.assertEqual(event.action, "payment.upload_confirmed")
        self.assertEqual(event.actor, self.admin)

    def test_filter_by_action(self) -> None:
        record_audit_event(
            actor=self.admin, action="action_a",
            target_type="X", target_id="1",
        )
        record_audit_event(
            actor=self.admin, action="action_b",
            target_type="X", target_id="2",
        )
        self.assertEqual(get_audit_events(action="action_a").count(), 1)

    def test_filter_by_target_type(self) -> None:
        record_audit_event(
            actor=self.admin, action="a1",
            target_type="Foo", target_id="1",
        )
        record_audit_event(
            actor=self.admin, action="a2",
            target_type="Bar", target_id="2",
        )
        self.assertEqual(get_audit_events(target_type="Foo").count(), 1)

    def test_filter_by_actor_id(self) -> None:
        other_user = User.objects.create_user(
            email="other@example.com", password="pass", is_staff=True
        )
        record_audit_event(
            actor=self.admin, action="act",
            target_type="T", target_id="1",
        )
        record_audit_event(
            actor=other_user, action="act",
            target_type="T", target_id="2",
        )
        self.assertEqual(get_audit_events(actor_id=self.admin.id).count(), 1)


class AuditViewTests(TestCase):
    def setUp(self) -> None:
        self.super_group, _ = Group.objects.get_or_create(name=GROUP_SUPER_ADMIN)
        self.admin_user = User.objects.create_user(
            email="super@example.com", password="pass", is_staff=True
        )
        self.admin_user.groups.add(self.super_group)
        self.client.force_login(self.admin_user)

    def test_list_returns_200(self) -> None:
        record_audit_event(
            actor=self.admin_user, action="test.action",
            target_type="Test", target_id="1",
        )
        response = self.client.get(reverse("audit:list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "test.action")

    def test_detail_returns_200(self) -> None:
        event = record_audit_event(
            actor=self.admin_user, action="detail.action",
            target_type="Detail", target_id="99",
        )
        response = self.client.get(reverse("audit:detail", args=[event.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "detail.action")

    def test_malformed_actor_id_does_not_crash(self) -> None:
        response = self.client.get(reverse("audit:list") + "?actor_id=x")
        self.assertEqual(response.status_code, 200)

    def test_filter_by_action_from_view(self) -> None:
        record_audit_event(
            actor=self.admin_user, action="view.action_a",
            target_type="T", target_id="1",
        )
        record_audit_event(
            actor=self.admin_user, action="view.action_b",
            target_type="T", target_id="2",
        )
        response = self.client.get(reverse("audit:list") + "?action=view.action_a")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "view.action_a")
        self.assertNotContains(response, "view.action_b")

    def test_filter_by_actor_id_from_view(self) -> None:
        other_user = User.objects.create_user(
            email="actor2@example.com", password="pass", is_staff=True
        )
        other_user.groups.add(self.super_group)
        record_audit_event(
            actor=self.admin_user, action="act.admin",
            target_type="T", target_id="1",
        )
        record_audit_event(
            actor=other_user, action="act.other",
            target_type="T", target_id="2",
        )
        response = self.client.get(
            reverse("audit:list") + f"?actor_id={self.admin_user.id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "act.admin")
        self.assertNotContains(response, "act.other")

    def test_filter_by_date_range_from_view(self) -> None:
        record_audit_event(
            actor=self.admin_user, action="old.event",
            target_type="T", target_id="1",
        )
        response = self.client.get(
            reverse("audit:list") + "?date_from=2099-01-01&date_to=2099-12-31"
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "old.event")

    def test_filter_by_target_type_from_view(self) -> None:
        record_audit_event(
            actor=self.admin_user, action="target.action",
            target_type="SpecificModel", target_id="1",
        )
        record_audit_event(
            actor=self.admin_user, action="other.action",
            target_type="OtherModel", target_id="2",
        )
        response = self.client.get(
            reverse("audit:list") + "?target_type=SpecificModel"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SpecificModel")
        self.assertNotContains(response, "OtherModel")

    def test_actor_dropdown_in_template_context(self) -> None:
        response = self.client.get(reverse("audit:list"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("actor_choices", response.context)
        self.assertIn(self.admin_user, response.context["actor_choices"])

    def test_invalid_date_returns_200(self) -> None:
        response = self.client.get(
            reverse("audit:list") + "?date_from=not-a-date&date_to=also-invalid"
        )
        self.assertEqual(response.status_code, 200)

    def test_invalid_date_range_returns_200(self) -> None:
        """A well-formed but non-existent date like 2026-13-01 must not crash."""
        response = self.client.get(
            reverse("audit:list") + "?date_from=2026-13-01&date_to=2026-01-32"
        )
        self.assertEqual(response.status_code, 200)
