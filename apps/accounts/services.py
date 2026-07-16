from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import datetime
from typing import Any

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.audit.constants import ACTION_USER_IMPORT_CONFIRMED, ACTION_USER_IMPORT_PREVIEWED
from apps.audit.models import AuditEvent
from apps.communications.constants import TEMPLATE_ACTIVATION
from apps.communications.services import send_templated_email
from apps.students.identity import normalize_identity_number, validate_identity_number
from apps.students.models import StudentProfile

from .constants import (
    GROUP_ADMIN,
    GROUP_MODERATOR,
    GROUP_STUDENT,
    GROUP_SUPER_ADMIN,
    GROUP_TEACHER,
    MATCH_METHOD_AMBIGUOUS,
    MATCH_METHOD_EMAIL,
    MATCH_METHOD_NATIONAL_ID,
    MATCH_METHOD_NONE,
    MATCH_METHOD_PHONE,
    REQUIRED_CSV_HEADERS,
    ROW_STATUS_AMBIGUOUS,
    ROW_STATUS_CREATED,
    ROW_STATUS_FAILED,
    ROW_STATUS_MATCHED,
    ROW_STATUS_PARSED,
    ROW_STATUS_UNMATCHED,
    ROW_STATUS_UPDATED,
    UPLOAD_STATUS_CONFIRMED,
    UPLOAD_STATUS_FAILED,
    UPLOAD_STATUS_PENDING,
    UPLOAD_STATUS_PREVIEWED,
    USER_STATUS_ACTIVE,
)
from .models import User, UserUpload, UserUploadRow

REQUIRED_PROFILE_FIELDS = ["date_of_birth", "gender", "address", "education"]


def normalize_phone(value: str) -> str:
    cleaned = re.sub(r"[^\d+]", "", value.strip())
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if cleaned.startswith("0") and not cleaned.startswith("+") and len(cleaned) == 11:
        cleaned = "+2" + cleaned
    return cleaned


def parse_date(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=timezone.get_current_timezone())
        except ValueError:
            continue
    return None


def validate_csv_content(content: bytes) -> list[dict[str, str]]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError(_("CSV file appears to be empty or malformed"))
    headers = [h.strip().lower() for h in reader.fieldnames]
    missing = [h for h in REQUIRED_CSV_HEADERS if h not in headers]
    if missing:
        raise ValueError(_("Missing required columns: {columns}").format(columns=", ".join(missing)))
    rows = []
    for row in reader:
        cleaned = {k.strip().lower(): (v or "").strip() for k, v in row.items()}
        rows.append(cleaned)
    return rows


def match_user(row: dict[str, str]) -> tuple[User | None, str, str]:
    email = row.get("email", "").strip().lower()
    country_code = _get_country_code(row)
    raw_national_id = row.get("national_id", "")
    national_id = normalize_identity_number(country_code, raw_national_id) if raw_national_id else ""
    phone = normalize_phone(row.get("phone", ""))
    candidates: dict[int, User] = {}
    sources: dict[int, str] = {}

    student_user_ids = set(User.objects.filter(groups__name=GROUP_STUDENT).values_list("id", flat=True))
    staff_group_names = [GROUP_SUPER_ADMIN, GROUP_ADMIN, GROUP_MODERATOR, GROUP_TEACHER]
    staff_user_ids = set(User.objects.filter(groups__name__in=staff_group_names).values_list("id", flat=True))

    def _is_importable(u: User) -> bool:
        return u.id in student_user_ids and u.id not in staff_user_ids

    if email:
        qs = User.objects.filter(email__iexact=email)
        if qs.count() == 1:
            u = qs.first()
            if _is_importable(u):
                candidates[u.id] = u
                sources[u.id] = "email"
            else:
                return None, MATCH_METHOD_AMBIGUOUS, f"Email {email} belongs to a staff account; cannot modify"

    if national_id:
        qs = User.objects.filter(national_id=national_id)
        if qs.count() > 1:
            return None, MATCH_METHOD_AMBIGUOUS, f"Multiple users match national ID {national_id}"
        if qs.count() == 1:
            u = qs.first()
            if _is_importable(u):
                candidates[u.id] = u
                sources[u.id] = sources.get(u.id, "") + "+national_id"
            else:
                return None, MATCH_METHOD_AMBIGUOUS, f"National ID {national_id} belongs to a staff account; cannot modify"

    if phone:
        qs = User.objects.filter(phone=phone)
        if qs.count() == 1:
            u = qs.first()
            if _is_importable(u):
                candidates[u.id] = u
                sources[u.id] = sources.get(u.id, "") + "+phone"
            else:
                return None, MATCH_METHOD_AMBIGUOUS, f"Phone {phone} belongs to a staff account; cannot modify"

    if not candidates:
        return None, MATCH_METHOD_NONE, "No student found matching any identifier"

    if len(candidates) > 1:
        return None, MATCH_METHOD_AMBIGUOUS, (
            f"Conflicting identifiers match {len(candidates)} different users"
        )

    match = next(iter(candidates.values()))
    method_str = sources.get(match.id, "")
    if "email" in method_str:
        method = MATCH_METHOD_EMAIL
    elif "national_id" in method_str:
        method = MATCH_METHOD_NATIONAL_ID
    elif "phone" in method_str:
        method = MATCH_METHOD_PHONE
    else:
        method = MATCH_METHOD_NONE
    return match, method, ""


def _get_country_code(row: dict[str, str]) -> str:
    return (row.get("country_code", "") or "").strip().upper() or "EG"


def _parse_row_data(row: dict[str, str], row_number: int) -> dict[str, Any]:
    email = row.get("email", "").strip().lower()
    full_name = row.get("full_name", "").strip()
    if not email:
        return {"status": ROW_STATUS_FAILED, "error_message": str(_("Empty email"))}
    if not full_name:
        return {"status": ROW_STATUS_FAILED, "error_message": str(_("Empty full name"))}
    country_code = _get_country_code(row)
    raw_national_id = row.get("national_id", "")
    national_id = normalize_identity_number(country_code, raw_national_id) if raw_national_id else ""
    if raw_national_id:
        try:
            validate_identity_number(country_code, raw_national_id)
        except ValidationError as e:
            return {"status": ROW_STATUS_FAILED, "error_message": str(e)}
    phone = normalize_phone(row.get("phone", ""))
    return {
        "raw_data": row,
        "normalized_email": email,
        "normalized_national_id": national_id,
        "normalized_phone": phone,
        "status": ROW_STATUS_PARSED,
        "error_message": "",
    }


@transaction.atomic
def create_upload(original_filename: str, content: bytes, uploaded_by) -> UserUpload:
    rows = validate_csv_content(content)
    fhash = hashlib.sha256(content).hexdigest()
    upload = UserUpload.objects.create(
        uploaded_by=uploaded_by,
        original_filename=original_filename,
        file_hash=fhash,
        status=UPLOAD_STATUS_PENDING,
        started_at=timezone.now(),
        total_rows=len(rows),
    )
    for i, row in enumerate(rows, start=1):
        parsed = _parse_row_data(row, i)
        UserUploadRow.objects.create(
            upload=upload,
            row_number=i,
            **parsed,
        )
    return upload


def preview_upload(upload_id: int) -> UserUpload:
    upload = UserUpload.objects.get(id=upload_id)
    errors: list[str] = []
    matched = unmatched = failed = 0

    rows = list(upload.rows.filter(status=ROW_STATUS_PARSED).order_by("row_number"))
    seen_emails: dict[str, int] = {}
    seen_national_ids: dict[str, int] = {}
    seen_phones: dict[str, int] = {}
    row_by_email: dict[str, UserUploadRow] = {}
    row_by_nid: dict[str, UserUploadRow] = {}
    row_by_phone: dict[str, UserUploadRow] = {}

    for row in rows:
        email = row.normalized_email.lower() if row.normalized_email else ""
        nid = row.normalized_national_id
        phone = row.normalized_phone
        if email and email in seen_emails:
            first_row = row_by_email[email]
            first_row.status = ROW_STATUS_AMBIGUOUS
            first_row.error_message = f"Duplicate email within file (row {row.row_number})"
            first_row.save(update_fields=["status", "error_message"])
            errors.append(f"Row {first_row.row_number}: {first_row.error_message}")

            row.status = ROW_STATUS_AMBIGUOUS
            row.error_message = f"Duplicate email within file (row {first_row.row_number})"
            row.save(update_fields=["status", "error_message"])
            failed += 1
            errors.append(f"Row {row.row_number}: {row.error_message}")
            continue
        if nid and nid in seen_national_ids:
            first_row = row_by_nid[nid]
            first_row.status = ROW_STATUS_AMBIGUOUS
            first_row.error_message = f"Duplicate national ID within file (row {row.row_number})"
            first_row.save(update_fields=["status", "error_message"])
            errors.append(f"Row {first_row.row_number}: {first_row.error_message}")

            row.status = ROW_STATUS_AMBIGUOUS
            row.error_message = f"Duplicate national ID within file (row {first_row.row_number})"
            row.save(update_fields=["status", "error_message"])
            failed += 1
            errors.append(f"Row {row.row_number}: {row.error_message}")
            continue
        if phone and phone in seen_phones:
            first_row = row_by_phone[phone]
            first_row.status = ROW_STATUS_AMBIGUOUS
            first_row.error_message = f"Duplicate phone within file (row {row.row_number})"
            first_row.save(update_fields=["status", "error_message"])
            errors.append(f"Row {first_row.row_number}: {first_row.error_message}")

            row.status = ROW_STATUS_AMBIGUOUS
            row.error_message = f"Duplicate phone within file (row {first_row.row_number})"
            row.save(update_fields=["status", "error_message"])
            failed += 1
            errors.append(f"Row {row.row_number}: {row.error_message}")
            continue
        seen_emails[email] = row.row_number
        row_by_email[email] = row
        if nid:
            seen_national_ids[nid] = row.row_number
            row_by_nid[nid] = row
        if phone:
            seen_phones[phone] = row.row_number
            row_by_phone[phone] = row
        data = row.raw_data
        user, method, error = match_user(data)
        if user:
            row.matched_user = user
            row.match_method = method
            row.status = ROW_STATUS_MATCHED
            matched += 1
        else:
            if method == MATCH_METHOD_NONE:
                # New user — validate required profile fields
                missing = [f for f in REQUIRED_PROFILE_FIELDS if not data.get(f, "").strip()]
                if missing:
                    row.status = ROW_STATUS_FAILED
                    row.error_message = f"Missing required profile fields: {', '.join(missing)}"
                    failed += 1
                else:
                    row.status = ROW_STATUS_UNMATCHED
                    unmatched += 1
            else:
                row.status = ROW_STATUS_FAILED if method != MATCH_METHOD_AMBIGUOUS else ROW_STATUS_AMBIGUOUS
                row.error_message = error
                failed += 1
            errors.append(f"Row {row.row_number}: {row.error_message or error}")
        row.save(update_fields=["matched_user", "match_method", "status", "error_message"])

    matched = upload.rows.filter(status=ROW_STATUS_MATCHED).count()
    unmatched = upload.rows.filter(status=ROW_STATUS_UNMATCHED).count()
    failed = upload.rows.filter(status__in=[ROW_STATUS_AMBIGUOUS, ROW_STATUS_FAILED]).count()
    upload.matched_rows = matched
    upload.unmatched_rows = unmatched
    upload.failed_rows = failed
    upload.status = UPLOAD_STATUS_PREVIEWED if failed == 0 else UPLOAD_STATUS_FAILED
    upload.error_summary = "\n".join(errors) if errors else ""
    upload.save(update_fields=["matched_rows", "unmatched_rows", "failed_rows", "status", "error_summary"])

    AuditEvent.objects.create(
        actor=upload.uploaded_by,
        action=ACTION_USER_IMPORT_PREVIEWED,
        target_type="UserUpload",
        target_id=str(upload.id),
        metadata={"matched": matched, "unmatched": unmatched, "failed": failed, "errors": errors},
    )
    return upload


def _build_student_profile_data(row_raw: dict[str, str]) -> dict[str, Any]:
    country_code = _get_country_code(row_raw)
    raw_national_id = row_raw.get("national_id", "")
    profile_fields = {
        "full_name": row_raw.get("full_name", ""),
        "phone": normalize_phone(row_raw.get("phone", "")),
        "national_id": normalize_identity_number(country_code, raw_national_id) if raw_national_id else "",
        "country_code": country_code,
        "date_of_birth": None,
        "gender": row_raw.get("gender", ""),
        "address": row_raw.get("address", ""),
        "education": row_raw.get("education", ""),
        "specialization": row_raw.get("specialization", ""),
        "employer": row_raw.get("employer", ""),
        "current_job": row_raw.get("current_job", ""),
        "church": row_raw.get("church", ""),
        "priest_name": row_raw.get("priest_name", ""),
        "priest_contact": row_raw.get("priest_contact", ""),
    }
    dob = parse_date(row_raw.get("date_of_birth", ""))
    if dob:
        profile_fields["date_of_birth"] = dob.date()
    return {k: v for k, v in profile_fields.items() if v}


@transaction.atomic
def confirm_upload(upload_id: int, confirmed_by) -> UserUpload:
    upload = UserUpload.objects.get(id=upload_id)
    if upload.status != UPLOAD_STATUS_PREVIEWED:
        raise ValueError(_("Upload is not in previewed state"))
    if upload.matched_rows == 0 and upload.unmatched_rows == 0:
        raise ValueError(_("No rows matched or unmatched; nothing to import"))

    student_group = Group.objects.get(name=GROUP_STUDENT)
    errors: list[str] = []
    created = updated = 0

    for row in upload.rows.filter(status__in=[ROW_STATUS_MATCHED, ROW_STATUS_UNMATCHED]):
        data = row.raw_data
        try:
            if row.matched_user:
                user = row.matched_user
                user.full_name = data.get("full_name", user.full_name)
                user.phone = normalize_phone(data.get("phone", user.phone))
                user.national_id = row.normalized_national_id or user.national_id
                full_clean_needed = not (user.full_name and user.email)
                if full_clean_needed:
                    user.full_name = user.full_name or user.email
                user.save(update_fields=["full_name", "phone", "national_id", "updated_at"])
                row.action = "update"
                updated += 1
            else:
                email = row.normalized_email
                full_name = data.get("full_name", "")
                user = User.objects.create_user(
                    email=email,
                    password=None,
                    full_name=full_name,
                    phone=row.normalized_phone,
                    national_id=row.normalized_national_id,
                    status=USER_STATUS_ACTIVE,
                    is_active=True,
                )
                user.groups.add(student_group)
                row.action = "create"
                created += 1

            profile_data = _build_student_profile_data(data)
            if profile_data:
                StudentProfile.objects.update_or_create(
                    user=user,
                    defaults=profile_data,
                )

            row.matched_user = user
            row.status = ROW_STATUS_UPDATED if row.action == "update" else ROW_STATUS_CREATED
            row.save(update_fields=["matched_user", "status", "action"])
        except Exception as e:
            errors.append(f"Row {row.row_number}: {e}")

    if errors:
        raise ValueError(_("Import failed for {count} row(s)").format(count=len(errors)))

    upload.created_rows = created
    upload.updated_rows = updated
    upload.status = UPLOAD_STATUS_CONFIRMED
    upload.completed_at = timezone.now()
    upload.save(update_fields=["created_rows", "updated_rows", "failed_rows", "status", "completed_at"])

    AuditEvent.objects.create(
        actor=confirmed_by,
        action=ACTION_USER_IMPORT_CONFIRMED,
        target_type="UserUpload",
        target_id=str(upload.id),
        metadata={"created": created, "updated": updated, "errors": errors},
    )
    return upload


def send_activation_email(user, password: str | None = None) -> None:
    from apps.applications.services import normalize_phone as application_normalize_phone

    from .tokens import generate_activation_token

    uidb64, token = generate_activation_token(user)
    activation_url = settings.SITE_URL + reverse("accounts:activate", kwargs={"uidb64": uidb64, "token": token})
    full_name = getattr(user, "full_name", "") or ""
    password = password or application_normalize_phone(user.phone or "")
    send_templated_email(
        TEMPLATE_ACTIVATION, user.email,
        {"full_name": full_name, "activation_url": activation_url, "email": user.email, "password": password},
        sent_by=None,
    )
