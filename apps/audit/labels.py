"""Human-readable labels for audit rows.

Audit history must stay readable even when a user, assessment, or role is
renamed or deleted. Labels therefore prefer the immutable snapshot stored on
the event and only fall back to the related object. No function here performs
database queries.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from apps.accounts.constants import GROUP_SUPER_ADMIN, role_display_label

from .constants import (
    ACTION_SECTION_PREFIXES,
    AUDIT_ACTION_CHOICES,
    AUDIT_SECTION_CHOICES,
    LEGACY_ACTION_LABELS,
    META_KEY_LABELS,
    OUTCOME_CHOICES,
    SECTION_ADMISSIONS,
    SECTION_SETTINGS,
    SOURCE_CHOICES,
    SOURCE_SYSTEM,
    SOURCE_USER,
    TARGET_TYPE_LABELS,
)

_ACTION_LABEL_MAP = dict(AUDIT_ACTION_CHOICES)
_SECTION_LABEL_MAP = dict(AUDIT_SECTION_CHOICES)
_SOURCE_LABEL_MAP = dict(SOURCE_CHOICES)
_OUTCOME_LABEL_MAP = dict(OUTCOME_CHOICES)

_SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "credential",
    "authorization",
    "session_key",
    "session_token",
    "sessionid",
    "api_key",
    "smtp_password",
    "national_id",
    "phone",
    "address",
)
_PASSWORD_FLAG_KEYS = {"password_reset", "password_changed", "has_password"}

# Values that commonly appear in metadata. These are intentionally generic:
# domain-specific vocabularies add their own labels where they matter.
_VALUE_LABELS = {
    "active": _("Active"),
    "inactive": _("Inactive"),
    "pending": _("Pending"),
    "approved": _("Approved"),
    "rejected": _("Rejected"),
    "submitted": _("Submitted"),
    "under_review": _("Under review"),
    "documents_requested": _("Documents requested"),
    "documents_submitted": _("Documents submitted"),
    "payment_pending": _("Payment pending"),
    "payment_confirmed": _("Payment confirmed"),
    "paid": _("Paid"),
    "account_activated": _("Account activated"),
    "activated": _("Activated"),
    "entrance_submitted": _("Entrance exam submitted"),
    "interview_requested": _("Interview requested"),
    "interview_completed": _("Interview completed"),
    "accepted": _("Accepted"),
    "waitlisted": _("Waitlisted"),
    "withdrawn": _("Withdrawn"),
    "cancelled": _("Cancelled"),
    "canceled": _("Cancelled"),
    "expired": _("Expired"),
    "completed": _("Completed"),
    "graduated": _("Graduated"),
    "passed": _("Passed"),
    "failed": _("Failed"),
    "released": _("Released"),
    "hidden": _("Hidden"),
    "queued": _("Queued"),
    "processing": _("Processing"),
    "sent": _("Sent"),
    "draft": _("Draft"),
    "open": _("Open"),
    "closed": _("Closed"),
    "scheduled": _("Scheduled"),
    "present": _("Present"),
    "absent": _("Absent"),
    "late": _("Late"),
    "excused": _("Excused"),
    "not_applicable": _("Not applicable"),
    "automatic": _("Automatic"),
    "manual": _("Manual"),
    "semester_exam": _("Semester exam"),
    "course_quiz": _("Course quiz"),
    "homework": _("Homework"),
    "diploma": _("Diploma"),
    "course": _("Course"),
    "resend_rejection": _("Rejection resend"),
    "invite_to_entrance": _("Entrance invitation"),
    "exam_result": _("Entrance result"),
    "added": _("Added"),
    "changed": _("Changed"),
    "deleted": _("Deleted"),
    "success": _("Success"),
    "failure": _("Failure"),
    "denied": _("Denied"),
    "user": _("User"),
    "system": _("System"),
    "task": _("Background job"),
    "public": _("Public form"),
}

_ADMIN_MODEL_SECTIONS = {
    "accounts": "accounts",
    "applications": SECTION_ADMISSIONS,
    "entrance_exams": SECTION_ADMISSIONS,
    "intake_forms": SECTION_ADMISSIONS,
    "academics": "academics",
    "assessments": "assessments",
    "attendance": "attendance",
    "progression": "progression",
    "payments": "payments",
    "certificates": "certificates",
    "communications": "communications",
    "core": SECTION_SETTINGS,
    "audit": SECTION_SETTINGS,
}


def _translate(value: Any) -> str:
    return str(value) if value is not None else ""


def humanize_identifier(value: object) -> str:
    """Turn a snake_case/CamelCase identifier into a readable label."""
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = text.replace("_", " ").replace("-", " ").strip()
    return " ".join(part.capitalize() for part in text.split())


def humanize_action_code(action: str) -> str:
    """Readable fallback for a legacy or unforeseen action code.

    The result is never the raw dotted code. Unknown values degrade to a
    sentence-like label instead of exposing implementation identifiers.
    """
    action = str(action or "").strip()
    if not action:
        return gettext("Unknown action")
    namespace, separator, name = action.partition(".")
    label = humanize_identifier(name or action)
    if separator and namespace:
        return f"{humanize_identifier(namespace)}: {label}"
    return label


def action_label(action: str) -> str:
    canonical = _ACTION_LABEL_MAP.get(action)
    if canonical is not None:
        return _translate(canonical)
    legacy = LEGACY_ACTION_LABELS.get(action)
    if legacy is not None:
        return _translate(legacy)
    return humanize_action_code(action)


def action_section(action: str) -> str:
    for prefix, section in ACTION_SECTION_PREFIXES.items():
        if action == prefix or action.startswith(prefix):
            return section
    return SECTION_SETTINGS


def section_label(section: str) -> str:
    label = _SECTION_LABEL_MAP.get(section)
    return _translate(label) if label is not None else humanize_identifier(section)


def source_label(source: str) -> str:
    label = _SOURCE_LABEL_MAP.get(source)
    return _translate(label) if label is not None else humanize_identifier(source)


def outcome_label(outcome: str) -> str:
    label = _OUTCOME_LABEL_MAP.get(outcome)
    return _translate(label) if label is not None else humanize_identifier(outcome)


def target_type_label(target_type: str) -> str:
    label = TARGET_TYPE_LABELS.get(target_type)
    return _translate(label) if label is not None else humanize_identifier(target_type)


def metadata_label(key: object) -> str:
    normalized = str(key or "")
    label = META_KEY_LABELS.get(normalized)
    return _translate(label) if label is not None else humanize_identifier(normalized)


def value_label(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return gettext("Yes") if value else gettext("No")
    if isinstance(value, str):
        normalized = value.strip().casefold()
        label = _VALUE_LABELS.get(normalized)
        if label is not None:
            return _translate(label)
        if re.fullmatch(r"[a-z][a-z0-9_.-]*", value.strip()) and ("_" in value or "." in value):
            return humanize_identifier(value)
        return value or "—"
    return str(value)


def is_sensitive_key(key: object) -> bool:
    normalized = str(key or "").casefold()
    if normalized in _PASSWORD_FLAG_KEYS:
        return False
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def safe_metadata(value: Any) -> Any:
    """Redact secret-like keys before persistence/rendering.

    The audit service applies this on write, and the view applies it again so
    historic payloads cannot leak values added before this guard existed.
    """
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, nested_value in value.items():
            if is_sensitive_key(key):
                safe[str(key)] = gettext("Sensitive value hidden")
            else:
                safe[str(key)] = safe_metadata(nested_value)
        return safe
    if isinstance(value, list):
        return [safe_metadata(item) for item in value]
    return value


def json_safe(value: Any) -> Any:
    """Convert audit metadata into JSON-serializable values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_safe(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return str(value)


def _display_value(value: Any, *, key: object = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return value_label(value)
    if key == "changed_fields" and isinstance(value, list):
        return ", ".join(metadata_label(item) for item in value)
    if isinstance(value, (dict, list)):
        return json.dumps(safe_metadata(value), ensure_ascii=False, sort_keys=True, indent=2)
    return value_label(value)


def display_metadata(metadata: object) -> list[dict[str, object]]:
    """Build template-ready key/value rows for an audit event."""
    if not isinstance(metadata, dict):
        return []
    rows: list[dict[str, object]] = []
    for key, value in metadata.items():
        sensitive = is_sensitive_key(key)
        rendered = gettext("Sensitive value hidden") if sensitive else _display_value(value, key=key)
        rows.append(
            {
                "key": str(key),
                "label": metadata_label(key),
                "value": rendered,
                "is_long": len(rendered) > 120 or isinstance(value, (dict, list)),
                "sensitive": sensitive,
            }
        )
    return rows


def actor_display(event: Any) -> str:
    snapshot = getattr(event, "actor_label", "") or ""
    if snapshot:
        return snapshot
    actor = getattr(event, "actor", None)
    if actor is not None:
        return str(getattr(actor, "full_name", "") or getattr(actor, "email", "") or actor)
    if getattr(event, "source", "") == "public":
        return gettext("Public")
    return gettext("System")


def actor_role_display(event: Any) -> str:
    snapshot = getattr(event, "actor_role", "") or ""
    if snapshot:
        return role_display_label(snapshot)
    actor = getattr(event, "actor", None)
    role = getattr(actor, "primary_role", "") if actor is not None else ""
    if not role and getattr(actor, "is_superuser", False):
        role = GROUP_SUPER_ADMIN
    return role_display_label(role) if role else ""


def target_display(event: Any) -> str:
    label = getattr(event, "target_label", "") or ""
    if label:
        return label
    target_type = getattr(event, "target_type", "") or ""
    return target_type_label(target_type) if target_type else gettext("System action")


def subject_display(event: Any) -> str:
    label = getattr(event, "subject_label", "") or ""
    if label:
        return label
    subject_type = getattr(event, "subject_type", "") or ""
    return target_type_label(subject_type) if subject_type else ""


def admin_section_for_app_label(app_label: str) -> str:
    return _ADMIN_MODEL_SECTIONS.get(str(app_label or ""), SECTION_SETTINGS)


def default_source(actor: Any) -> str:
    return SOURCE_USER if actor is not None else SOURCE_SYSTEM
