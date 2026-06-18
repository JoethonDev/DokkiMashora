"""Role and status constants used across the LMS."""

from __future__ import annotations

# Role group names.
GROUP_SUPER_ADMIN = "Super Admin"
GROUP_ADMIN = "Admin"
GROUP_MODERATOR = "Moderator"
GROUP_TEACHER = "Teacher"
GROUP_STUDENT = "Student"

ALL_ROLE_GROUPS = (
    GROUP_SUPER_ADMIN,
    GROUP_ADMIN,
    GROUP_MODERATOR,
    GROUP_TEACHER,
    GROUP_STUDENT,
)

STAFF_ROLE_GROUPS = (
    GROUP_SUPER_ADMIN,
    GROUP_ADMIN,
    GROUP_MODERATOR,
    GROUP_TEACHER,
)

# Mapping from group name to a stable slug used in code checks and tests.
ROLE_SLUGS = {
    GROUP_SUPER_ADMIN: "super_admin",
    GROUP_ADMIN: "admin",
    GROUP_MODERATOR: "moderator",
    GROUP_TEACHER: "teacher",
    GROUP_STUDENT: "student",
}

# User status choices.
USER_STATUS_ACTIVE = "active"
USER_STATUS_INACTIVE = "inactive"
USER_STATUS_SUSPENDED = "suspended"

USER_STATUS_CHOICES = (
    (USER_STATUS_ACTIVE, "Active"),
    (USER_STATUS_INACTIVE, "Inactive"),
    (USER_STATUS_SUSPENDED, "Suspended"),
)
