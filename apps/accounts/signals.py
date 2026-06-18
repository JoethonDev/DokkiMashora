"""Signal handlers for the accounts app."""

from __future__ import annotations

from django.contrib.auth.models import Group
from django.db.models.signals import post_migrate
from django.dispatch import receiver

from .constants import ALL_ROLE_GROUPS, GROUP_SUPER_ADMIN


@receiver(post_migrate)
def ensure_role_groups(sender, **kwargs) -> None:
    """Create the default role groups after the auth app migrates."""
    if sender.label != "auth":
        return
    for name in ALL_ROLE_GROUPS:
        Group.objects.get_or_create(name=name)
    # Superusers are automatically super admins, so no extra group is required,
    # but we still surface the group for downstream permission lookups.


def assign_default_groups_for_superuser(user) -> None:
    """Add the Super Admin group to newly created superusers."""
    if not user.is_superuser:
        return
    group, _ = Group.objects.get_or_create(name=GROUP_SUPER_ADMIN)
    user.groups.add(group)
