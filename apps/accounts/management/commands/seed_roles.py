"""Seed the default role groups and demo accounts."""

from __future__ import annotations

import os
import secrets
from collections.abc import Sequence

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.constants import (
    ALL_ROLE_GROUPS,
    GROUP_ADMIN,
    GROUP_MODERATOR,
    GROUP_REVIEWER,
    GROUP_SECRETARY,
    GROUP_STUDENT,
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

        if getattr(settings, "PRODUCTION_DEPLOYMENT", False):
            raise CommandError(
                "Refusing to create demo accounts: PRODUCTION_DEPLOYMENT is enabled."
            )

        if os.environ.get("DJANGO_SEED_DEMO", "false").lower() not in {"1", "true", "yes"}:
            self.stdout.write("Demo users skipped (DJANGO_SEED_DEMO is not enabled).")
            return

        # S2-29: never use a predictable, shared password. The operator supplies
        # an explicit secret via DJANGO_SEED_DEMO_PASSWORD; otherwise a random
        # password is generated per run and printed so local QA can log in.
        demo_password = os.environ.get("DJANGO_SEED_DEMO_PASSWORD", "").strip()
        generated_password = not demo_password
        if generated_password:
            demo_password = secrets.token_urlsafe(16)

        user_model = get_user_model()
        demo_users: Sequence[tuple[str, str, str]] = (
            ("admin@dokkimashora.test", "Admin User", GROUP_ADMIN),
            ("moderator@dokkimashora.test", "Moderator User", GROUP_MODERATOR),
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
                    user.set_password(demo_password)
                    user.save()
                user.groups.add(Group.objects.get(name=group_name))
            self.stdout.write(
                self.style.SUCCESS(f"Demo user ensured: {email} ({group_name})")
                if created
                else f"Demo user exists: {email} ({group_name})"
            )

        if generated_password:
            self.stdout.write(self.style.WARNING(
                f"No DJANGO_SEED_DEMO_PASSWORD was set; created demo users share the "
                f"generated password printed below (log in, then rotate it):\n{demo_password}"
            ))
