"""Tests for the custom user model and role groups."""

from __future__ import annotations

from django.contrib.auth.models import Group
from django.test import TestCase

from apps.accounts.constants import (
    GROUP_ADMIN,
    GROUP_MODERATOR,
    GROUP_STUDENT,
    GROUP_SUPER_ADMIN,
    GROUP_TEACHER,
)
from apps.accounts.models import User


class UserManagerTests(TestCase):
    def test_create_user_normalizes_email(self) -> None:
        user = User.objects.create_user(email="Foo@Example.COM", password="x")
        self.assertEqual(user.email, "foo@example.com")

    def test_create_user_requires_email(self) -> None:
        with self.assertRaises(ValueError):
            User.objects.create_user(email="", password="x")

    def test_create_superuser_sets_flags(self) -> None:
        admin = User.objects.create_superuser(email="root@example.com", password="x")
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.is_active)


class RoleGroupSignalTests(TestCase):
    def test_all_role_groups_exist_after_migrate(self) -> None:
        for name in (GROUP_SUPER_ADMIN, GROUP_ADMIN, GROUP_MODERATOR, GROUP_TEACHER, GROUP_STUDENT):
            self.assertTrue(Group.objects.filter(name=name).exists())


class PrimaryRoleTests(TestCase):
    def setUp(self) -> None:
        Group.objects.get_or_create(name=GROUP_TEACHER)
        Group.objects.get_or_create(name=GROUP_STUDENT)
        Group.objects.get_or_create(name=GROUP_ADMIN)

    def test_primary_role_returns_highest_staff_role(self) -> None:
        user = User.objects.create_user(email="u1@example.com", password="x")
        user.groups.add(Group.objects.get(name=GROUP_TEACHER))
        self.assertEqual(user.primary_role, GROUP_TEACHER)

    def test_primary_role_empty_for_user_with_no_group(self) -> None:
        user = User.objects.create_user(email="u2@example.com", password="x")
        self.assertEqual(user.primary_role, "")

    def test_superuser_is_staff_member(self) -> None:
        admin = User.objects.create_superuser(email="r@example.com", password="x")
        self.assertTrue(admin.is_staff_member)
