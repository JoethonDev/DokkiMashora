"""Signal handlers for the accounts app."""

from __future__ import annotations

from django.apps import apps
from django.db.models.signals import post_migrate
from django.dispatch import receiver

from .constants import ALL_ROLE_GROUPS


@receiver(post_migrate)
def ensure_role_groups(sender, **kwargs) -> None:
    """Create the default role groups after the auth app migrates."""
    if sender.label != "auth":
        return
    group_model = apps.get_model("auth", "Group")
    for name in ALL_ROLE_GROUPS:
        group_model.objects.get_or_create(name=name)
    # Superusers are automatically super admins, so no extra group is required,
    # but we still surface the group for downstream permission lookups.
