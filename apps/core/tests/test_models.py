"""Tests for the TimeStampedModel base model."""

from __future__ import annotations

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User


class TimeStampedModelTests(TestCase):
    def test_timestamps_are_timezone_aware(self) -> None:
        user = User.objects.create_user(email="ts@example.com", password="x")
        self.assertIsNotNone(user.created_at)
        self.assertIsNotNone(user.updated_at)
        self.assertIsNotNone(user.created_at.tzinfo)
        self.assertIsNotNone(user.updated_at.tzinfo)

    def test_timestamps_default_to_now(self) -> None:
        before = timezone.now()
        user = User.objects.create_user(email="ts2@example.com", password="x")
        after = timezone.now()
        self.assertGreaterEqual(user.created_at, before - timezone.timedelta(seconds=1))
        self.assertLessEqual(user.created_at, after + timezone.timedelta(seconds=1))
