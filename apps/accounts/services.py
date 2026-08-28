from __future__ import annotations

import csv
import hashlib
import io
from typing import Any

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.db.models.deletion import CASCADE, PROTECT
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.audit.constants import (
    ACTION_USER_DELETED,
    ACTION_USER_IMPORT_CONFIRMED,
    ACTION_USER_IMPORT_PREVIEWED,
)
from apps.audit.services import record_audit_event
from apps.communications.constants import TEMPLATE_ACTIVATION
from apps.communications.services import queue_templated_email
from apps.core.dates import parse_date
from apps.core.phone import normalize_phone
from apps.students.identity import normalize_identity_number, validate_identity_number
from apps.students.models import StudentProfile

from .constants import (
    GROUP_STUDENT,
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
    STAFF_ROLE_GROUPS,
    UPLOAD_STATUS_CONFIRMED,
    UPLOAD_STATUS_FAILED,
    UPLOAD_STATUS_PENDING,
    UPLOAD_STATUS_PREVIEWED,
    USER_STATUS_ACTIVE,
)
from .models import User, UserUpload, UserUploadRow

REQUIRED_PROFILE_FIELDS = ["date_of_birth", "gender", "address", "education"]


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


def _load_import_user_scope() -> tuple[set[int], set[int]]:
    """Return student and staff user ids once per import run."""
    student_user_ids = set(User.objects.filter(groups__name=GROUP_STUDENT).values_list("id", flat=True))
    staff_user_ids = set(
        User.objects.filter(groups__name__in=STAFF_ROLE_GROUPS).values_list("id", flat=True)
    )
    return student_user_ids, staff_user_ids


def _load_match_indexes(
    emails: set[str], national_ids: set[str], phones: set[str]
) -> tuple[dict[str, list[User]], dict[str, list[User]], dict[str, list[User]]]:
    """Build in-memory identifier → candidate-users maps with a bounded query set.

    Emails are lowercased at creation (``UserManager``), so an exact ``__in``
    match is equivalent to the previous per-row ``email__iexact`` lookup.
    """
    email_index: dict[str, list[User]] = {}
    if emails:
        for u in User.objects.filter(email__in=emails).only("id", "email", "phone", "national_id"):
            email_index.setdefault(u.email.lower(), []).append(u)
    national_id_index: dict[str, list[User]] = {}
    if national_ids:
        for u in User.objects.filter(national_id__in=national_ids).only("id", "email", "phone", "national_id"):
            national_id_index.setdefault(u.national_id, []).append(u)
    phone_index: dict[str, list[User]] = {}
    if phones:
        for profile in (
            StudentProfile.objects.filter(phone__in=phones)
            .select_related("user")
            .only("id", "user_id", "phone")
        ):
            phone_index.setdefault(profile.phone, []).append(profile.user)
    return email_index, national_id_index, phone_index


def _match_from_indexes(
    *,
    email: str,
    national_id: str,
    phone: str,
    email_index: dict[str, list[User]],
    national_id_index: dict[str, list[User]],
    phone_index: dict[str, list[User]],
    student_user_ids: set[int],
    staff_user_ids: set[int],
) -> tuple[User | None, str, str]:
    candidates: dict[int, User] = {}
    sources: dict[int, str] = {}

    def _is_importable(u: User) -> bool:
        return u.id in student_user_ids and u.id not in staff_user_ids

    if email:
        matches = email_index.get(email, [])
        if len(matches) == 1:
            u = matches[0]
            if _is_importable(u):
                candidates[u.id] = u
                sources[u.id] = "email"
            else:
                return None, MATCH_METHOD_AMBIGUOUS, f"Email {email} belongs to a staff account; cannot modify"

    if national_id:
        matches = national_id_index.get(national_id, [])
        if len(matches) > 1:
            return None, MATCH_METHOD_AMBIGUOUS, f"Multiple users match national ID {national_id}"
        if len(matches) == 1:
            u = matches[0]
            if _is_importable(u):
                candidates[u.id] = u
                sources[u.id] = sources.get(u.id, "") + "+national_id"
            else:
                return None, MATCH_METHOD_AMBIGUOUS, f"National ID {national_id} belongs to a staff account; cannot modify"

    if phone:
        matches = phone_index.get(phone, [])
        if len(matches) == 1:
            u = matches[0]
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


def match_user(row: dict[str, str]) -> tuple[User | None, str, str]:
    """Match a single CSV row to an existing student user (kept for callers)."""
    email = row.get("email", "").strip().lower()
    country_code = _get_country_code(row)
    raw_national_id = row.get("national_id", "")
    national_id = normalize_identity_number(country_code, raw_national_id) if raw_national_id else ""
    phone = normalize_phone(row.get("phone", ""))
    student_user_ids, staff_user_ids = _load_import_user_scope()
    email_index, national_id_index, phone_index = _load_match_indexes(
        {email} if email else set(),
        {national_id} if national_id else set(),
        {phone} if phone else set(),
    )
    return _match_from_indexes(
        email=email,
        national_id=national_id,
        phone=phone,
        email_index=email_index,
        national_id_index=national_id_index,
        phone_index=phone_index,
        student_user_ids=student_user_ids,
        staff_user_ids=staff_user_ids,
    )


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

    rows = list(upload.rows.filter(status=ROW_STATUS_PARSED).order_by("row_number"))
    student_user_ids, staff_user_ids = _load_import_user_scope()
    email_index, national_id_index, phone_index = _load_match_indexes(
        {r.normalized_email.lower() for r in rows if r.normalized_email},
        {r.normalized_national_id for r in rows if r.normalized_national_id},
        {r.normalized_phone for r in rows if r.normalized_phone},
    )
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
            errors.append(f"Row {first_row.row_number}: {first_row.error_message}")

            row.status = ROW_STATUS_AMBIGUOUS
            row.error_message = f"Duplicate email within file (row {first_row.row_number})"
            errors.append(f"Row {row.row_number}: {row.error_message}")
            continue
        if nid and nid in seen_national_ids:
            first_row = row_by_nid[nid]
            first_row.status = ROW_STATUS_AMBIGUOUS
            first_row.error_message = f"Duplicate national ID within file (row {row.row_number})"
            errors.append(f"Row {first_row.row_number}: {first_row.error_message}")

            row.status = ROW_STATUS_AMBIGUOUS
            row.error_message = f"Duplicate national ID within file (row {first_row.row_number})"
            errors.append(f"Row {row.row_number}: {row.error_message}")
            continue
        if phone and phone in seen_phones:
            first_row = row_by_phone[phone]
            first_row.status = ROW_STATUS_AMBIGUOUS
            first_row.error_message = f"Duplicate phone within file (row {row.row_number})"
            errors.append(f"Row {first_row.row_number}: {first_row.error_message}")

            row.status = ROW_STATUS_AMBIGUOUS
            row.error_message = f"Duplicate phone within file (row {first_row.row_number})"
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
        user, method, error = _match_from_indexes(
            email=email,
            national_id=nid,
            phone=phone,
            email_index=email_index,
            national_id_index=national_id_index,
            phone_index=phone_index,
            student_user_ids=student_user_ids,
            staff_user_ids=staff_user_ids,
        )
        if user:
            row.matched_user = user
            row.match_method = method
            row.status = ROW_STATUS_MATCHED
        else:
            if method == MATCH_METHOD_NONE:
                # New user — validate required profile fields
                missing = [f for f in REQUIRED_PROFILE_FIELDS if not data.get(f, "").strip()]
                if missing:
                    row.status = ROW_STATUS_FAILED
                    row.error_message = f"Missing required profile fields: {', '.join(missing)}"
                else:
                    row.status = ROW_STATUS_UNMATCHED
            else:
                row.status = ROW_STATUS_FAILED if method != MATCH_METHOD_AMBIGUOUS else ROW_STATUS_AMBIGUOUS
                row.error_message = error
            errors.append(f"Row {row.row_number}: {row.error_message or error}")

    now = timezone.now()
    for row in rows:
        row.updated_at = now
    upload.rows.bulk_update(rows, ["matched_user", "match_method", "status", "error_message", "updated_at"])

    matched = upload.rows.filter(status=ROW_STATUS_MATCHED).count()
    unmatched = upload.rows.filter(status=ROW_STATUS_UNMATCHED).count()
    failed = upload.rows.filter(status__in=[ROW_STATUS_AMBIGUOUS, ROW_STATUS_FAILED]).count()
    upload.matched_rows = matched
    upload.unmatched_rows = unmatched
    upload.failed_rows = failed
    upload.status = UPLOAD_STATUS_PREVIEWED if failed == 0 else UPLOAD_STATUS_FAILED
    upload.error_summary = "\n".join(errors) if errors else ""
    upload.save(update_fields=["matched_rows", "unmatched_rows", "failed_rows", "status", "error_summary"])

    record_audit_event(
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

    rows = list(
        upload.rows.filter(status__in=[ROW_STATUS_MATCHED, ROW_STATUS_UNMATCHED]).order_by("row_number")
    )
    if not rows:
        raise ValueError(_("No rows matched or unmatched; nothing to import"))

    update_rows = [row for row in rows if row.matched_user_id]
    create_rows = [row for row in rows if not row.matched_user_id]
    created = len(create_rows)
    updated = len(update_rows)

    try:
        # Matched users: fetch once and apply the CSV overrides.
        matched_users = {
            user.id: user
            for user in User.objects.filter(id__in=[row.matched_user_id for row in update_rows])
        }
        users_to_update = []
        for row in update_rows:
            user = matched_users.get(row.matched_user_id)
            if user is None:
                # The matched account no longer exists; treat as a new user.
                create_rows.append(row)
                created += 1
                updated -= 1
                continue
            data = row.raw_data
            user.full_name = data.get("full_name", user.full_name)
            user.national_id = row.normalized_national_id or user.national_id
            if not (user.full_name and user.email):
                user.full_name = user.full_name or user.email
            user.updated_at = timezone.now()
            users_to_update.append(user)
        if users_to_update:
            User.objects.bulk_update(users_to_update, ["full_name", "national_id", "updated_at"])

        # New users: create in bulk, then attach the Student group in bulk.
        new_users = []
        for row in create_rows:
            user = User(
                email=row.normalized_email,
                full_name=row.raw_data.get("full_name", ""),
                national_id=row.normalized_national_id,
                country_code=_get_country_code(row.raw_data),
                status=USER_STATUS_ACTIVE,
                is_active=True,
                is_staff=False,
                is_superuser=False,
            )
            user.set_unusable_password()
            new_users.append(user)
        if new_users:
            User.objects.bulk_create(new_users)
            student_group.user_set.add(*new_users)

        # Profiles: merge per-user CSV fields (later rows override earlier,
        # matching sequential update_or_create), then write in bulk.
        new_user_by_row = dict(zip(create_rows, new_users, strict=False))
        merged_profile_data: dict[int, dict[str, Any]] = {}
        for row in update_rows:
            user = matched_users.get(row.matched_user_id)
            if user is None:
                continue
            profile_data = _build_student_profile_data(row.raw_data)
            if profile_data:
                merged_profile_data.setdefault(user.id, {}).update(profile_data)
        for row in create_rows:
            profile_data = _build_student_profile_data(row.raw_data)
            if profile_data:
                merged_profile_data.setdefault(new_user_by_row[row].id, {}).update(profile_data)

        users_by_id = dict(matched_users)
        users_by_id.update({u.id: u for u in new_users})
        existing_profiles = {
            p.user_id: p for p in StudentProfile.objects.filter(user_id__in=merged_profile_data)
        }
        profile_fields: set[str] = set()
        profiles_to_update = []
        profiles_to_create = []
        for user_id, profile_data in merged_profile_data.items():
            if user_id in existing_profiles:
                profile = existing_profiles[user_id]
                for field, value in profile_data.items():
                    setattr(profile, field, value)
                profile.updated_at = timezone.now()
                profiles_to_update.append(profile)
                profile_fields.update(profile_data)
            else:
                profiles_to_create.append(StudentProfile(user=users_by_id[user_id], **profile_data))
        if profiles_to_update:
            StudentProfile.objects.bulk_update(
                profiles_to_update,
                list(profile_fields | {"updated_at"}),
            )
        if profiles_to_create:
            StudentProfile.objects.bulk_create(profiles_to_create)

        # Row audit records.
        for row in update_rows:
            user = matched_users.get(row.matched_user_id)
            if user is None:
                continue
            row.matched_user = user
            row.status = ROW_STATUS_UPDATED
            row.action = "update"
            row.updated_at = timezone.now()
        for row in create_rows:
            row.matched_user = new_user_by_row[row]
            row.status = ROW_STATUS_CREATED
            row.action = "create"
            row.updated_at = timezone.now()
        if rows:
            UserUploadRow.objects.bulk_update(rows, ["matched_user", "status", "action", "updated_at"])
    except Exception as e:  # noqa: BLE001
        errors.append(str(e))
        raise ValueError(_("Import failed for {count} row(s)").format(count=len(errors))) from None

    upload.created_rows = created
    upload.updated_rows = updated
    upload.status = UPLOAD_STATUS_CONFIRMED
    upload.completed_at = timezone.now()
    upload.save(update_fields=["created_rows", "updated_rows", "failed_rows", "status", "completed_at"])

    record_audit_event(
        actor=confirmed_by,
        action=ACTION_USER_IMPORT_CONFIRMED,
        target_type="UserUpload",
        target_id=str(upload.id),
        metadata={"created": created, "updated": updated, "errors": errors},
    )
    return upload


def send_activation_email(user, *, idempotency_key: str | None = None, sent_by=None) -> None:
    from .tokens import generate_activation_token

    uidb64, token = generate_activation_token(user)
    activation_url = settings.SITE_URL + reverse("accounts:activate", kwargs={"uidb64": uidb64, "token": token})
    full_name = getattr(user, "full_name", "") or ""
    # The initial password is never delivered by email (S2-24); the activation
    # link is the one-time token and the password is set during activation.
    queue_templated_email(
        TEMPLATE_ACTIVATION, user.email,
        {"full_name": full_name, "activation_url": activation_url, "email": user.email},
        sent_by=sent_by,
        idempotency_key=idempotency_key,
        reference=f"user:{user.pk}",
    )


def _has_cascade_or_protected_records(instance, *, excluded_models=()) -> bool:
    """Return whether deleting instance would remove or invalidate related records."""
    for relation in instance._meta.related_objects:
        if relation.on_delete not in (CASCADE, PROTECT) or relation.related_model in excluded_models:
            continue
        accessor = relation.get_accessor_name()
        if not accessor:
            continue
        try:
            related = getattr(instance, accessor)
        except ObjectDoesNotExist:
            continue
        if hasattr(related, "exists"):
            if related.exists():
                return True
        elif related is not None:
            return True
    return False


@transaction.atomic
def delete_student_account(user, actor) -> None:
    """Delete an unused student account without deleting historical records."""
    profile = StudentProfile.objects.filter(user=user).first()
    if profile is None:
        raise ValueError(_("This account has no student profile."))
    if _has_cascade_or_protected_records(profile) or _has_cascade_or_protected_records(
        user,
        excluded_models=(StudentProfile,),
    ):
        raise ValueError(_("This student cannot be deleted because historical records are linked to the account."))

    user_id = user.id
    email = user.email
    user.delete()
    record_audit_event(
        actor=actor,
        action=ACTION_USER_DELETED,
        target_type="user",
        target_id=str(user_id),
        metadata={"email": email, "student_account": True},
    )
