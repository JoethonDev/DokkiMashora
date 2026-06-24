"""Project-wide constants."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

ACTIVE = "active"
INACTIVE = "inactive"
ARCHIVED = "archived"

STATUS_CHOICES = (
    (ACTIVE, _("Active")),
    (INACTIVE, _("Inactive")),
    (ARCHIVED, _("Archived")),
)
