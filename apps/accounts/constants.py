"""Role and status constants used across the LMS."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

# Role group names.
GROUP_SUPER_ADMIN = "Super Admin"
GROUP_ADMIN = "Admin"
GROUP_MODERATOR = "Moderator"
GROUP_TEACHER = "Teacher"
GROUP_STUDENT = "Student"
GROUP_REVIEWER = "Reviewer"
GROUP_SECRETARY = "Secretary"

ALL_ROLE_GROUPS = (
    GROUP_SUPER_ADMIN,
    GROUP_ADMIN,
    GROUP_MODERATOR,
    GROUP_TEACHER,
    GROUP_STUDENT,
    GROUP_REVIEWER,
    GROUP_SECRETARY,
)

STAFF_ROLE_GROUPS = (
    GROUP_SUPER_ADMIN,
    GROUP_ADMIN,
    GROUP_MODERATOR,
    GROUP_TEACHER,
    GROUP_REVIEWER,
    GROUP_SECRETARY,
)

# Mapping from group name to a stable slug used in code checks and tests.
ROLE_SLUGS = {
    GROUP_SUPER_ADMIN: "super_admin",
    GROUP_ADMIN: "admin",
    GROUP_MODERATOR: "moderator",
    GROUP_TEACHER: "teacher",
    GROUP_STUDENT: "student",
    GROUP_REVIEWER: "reviewer",
    GROUP_SECRETARY: "secretary",
}

# User status choices.
USER_STATUS_ACTIVE = "active"
USER_STATUS_INACTIVE = "inactive"
USER_STATUS_SUSPENDED = "suspended"

USER_STATUS_CHOICES = (
    (USER_STATUS_ACTIVE, _("Active")),
    (USER_STATUS_INACTIVE, _("Inactive")),
    (USER_STATUS_SUSPENDED, _("Suspended")),
)

# Bulk upload statuses.
UPLOAD_STATUS_PENDING = "pending"
UPLOAD_STATUS_PREVIEWED = "previewed"
UPLOAD_STATUS_CONFIRMED = "confirmed"
UPLOAD_STATUS_FAILED = "failed"

UPLOAD_STATUS_CHOICES = (
    (UPLOAD_STATUS_PENDING, _("Pending")),
    (UPLOAD_STATUS_PREVIEWED, _("Previewed")),
    (UPLOAD_STATUS_CONFIRMED, _("Confirmed")),
    (UPLOAD_STATUS_FAILED, _("Failed")),
)

# Row statuses.
ROW_STATUS_PARSED = "parsed"
ROW_STATUS_MATCHED = "matched"
ROW_STATUS_UNMATCHED = "unmatched"
ROW_STATUS_AMBIGUOUS = "ambiguous"
ROW_STATUS_CREATED = "created"
ROW_STATUS_UPDATED = "updated"
ROW_STATUS_SKIPPED = "skipped"
ROW_STATUS_FAILED = "failed"

ROW_STATUS_CHOICES = (
    (ROW_STATUS_PARSED, _("Parsed")),
    (ROW_STATUS_MATCHED, _("Matched")),
    (ROW_STATUS_UNMATCHED, _("Unmatched")),
    (ROW_STATUS_AMBIGUOUS, _("Ambiguous")),
    (ROW_STATUS_CREATED, _("Created")),
    (ROW_STATUS_UPDATED, _("Updated")),
    (ROW_STATUS_SKIPPED, _("Skipped")),
    (ROW_STATUS_FAILED, _("Failed")),
)

MATCH_METHOD_EMAIL = "email"
MATCH_METHOD_NATIONAL_ID = "national_id"
MATCH_METHOD_PHONE = "phone"
MATCH_METHOD_NONE = "none"
MATCH_METHOD_AMBIGUOUS = "ambiguous"

MATCH_METHOD_CHOICES = (
    (MATCH_METHOD_EMAIL, _("Email")),
    (MATCH_METHOD_NATIONAL_ID, _("National ID")),
    (MATCH_METHOD_PHONE, _("Phone")),
    (MATCH_METHOD_NONE, _("None")),
    (MATCH_METHOD_AMBIGUOUS, _("Ambiguous")),
)

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

REQUIRED_CSV_HEADERS = ["email", "full_name"]
OPTIONAL_CSV_HEADERS = [
    "phone", "national_id", "date_of_birth", "gender", "address",
    "education", "specialization", "employer", "current_job",
    "church", "priest_name", "priest_contact",
]

ALL_CSV_HEADERS = REQUIRED_CSV_HEADERS + list(OPTIONAL_CSV_HEADERS)
