"""Seed the default role groups and demo accounts."""

from __future__ import annotations

import os
from collections.abc import Sequence

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.constants import (
    ALL_ROLE_GROUPS,
    GROUP_ADMIN,
    GROUP_MODERATOR,
    GROUP_REVIEWER,
    GROUP_SECRETARY,
    GROUP_STUDENT,
    GROUP_TEACHER,
)


class Command(BaseCommand):
    help = "Create the default role groups and a demo user for each role."

    def handle(self, *args, **options) -> None:
        for group_name in ALL_ROLE_GROUPS:
            group, created = Group.objects.get_or_create(name=group_name)
            self.stdout.write(
                self.style.SUCCESS(f"Group ensured: {group.name}")
                if created
                else f"Group exists: {group.name}"
            )

        if os.environ.get("DJANGO_SEED_DEMO", "true").lower() in {"0", "false", "no"}:
            self.stdout.write("Demo users skipped (DJANGO_SEED_DEMO=false).")
            return

        user_model = get_user_model()
        demo_users: Sequence[tuple[str, str, str]] = (
            ("admin@dokkimashora.test", "Admin User", GROUP_ADMIN),
            ("moderator@dokkimashora.test", "Moderator User", GROUP_MODERATOR),
            ("teacher@dokkimashora.test", "Teacher User", GROUP_TEACHER),
            ("reviewer@dokkimashora.test", "Reviewer User", GROUP_REVIEWER),
            ("secretary@dokkimashora.test", "Secretary User", GROUP_SECRETARY),
            ("student@dokkimashora.test", "Student User", GROUP_STUDENT),
        )
        for email, full_name, group_name in demo_users:
            with transaction.atomic():
                user, created = user_model.objects.get_or_create(
                    email=email,
                    defaults={"full_name": full_name, "is_active": True},
                )
                if created:
                    user.set_password("ChangeMe123!")
                    user.save()
                user.groups.add(Group.objects.get(name=group_name))
            self.stdout.write(
                self.style.SUCCESS(f"Demo user ensured: {email} ({group_name})")
                if created
                else f"Demo user exists: {email} ({group_name})"
            )
