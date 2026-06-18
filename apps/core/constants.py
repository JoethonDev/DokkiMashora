"""Project-wide constants."""

from __future__ import annotations

ACTIVE = "active"
INACTIVE = "inactive"
ARCHIVED = "archived"

STATUS_CHOICES = (
    (ACTIVE, "Active"),
    (INACTIVE, "Inactive"),
    (ARCHIVED, "Archived"),
)
