from __future__ import annotations

import csv
import hashlib
import io
from typing import Any

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, connection, transaction
from django.db.models.deletion import CASCADE, PROTECT
from django.db.models.functions import Upper
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.translation import gettext_lazy as _

from apps.audit.constants import (
    ACTION_USER_DELETED,
    ACTION_USER_IMPORT_CONFIRMED,
    ACTION_USER_IMPORT_PREVIEWED,
    SECTION_ACCOUNTS,
)
from apps.audit.services import record_audit_event
from apps.communications.constants import TEMPLATE_ACTIVATION
from apps.communications.services import queue_templated_email
from apps.core.dates import parse_date
from apps.core.phone import normalize_phone, validate_phone
from apps.students.identity import (
    COUNTRY_CHOICES,
    normalize_country_code,
    normalize_identity_number,
    validate_identity_number,
    validate_unique_identity_values,
)
from apps.students.models import StudentProfile

from .constants import (
    GROUP_STUDENT,
    MATCH_METHOD_AMBIGUOUS,
    MATCH_METHOD_EMAIL,
    MATCH_METHOD_NATIONAL_ID,
    MATCH_METHOD_NONE,
    MATCH_METHOD_PHONE,
    MAX_FILE_SIZE_BYTES,
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
)
from .models import User, UserUpload, UserUploadRow
from .tokens import generate_activation_token

MAX_IMPORT_ROWS = 1_000


def lock_identity_writes() -> None:
    """Serialize cross-table identity writes on PostgreSQL.

    Email, phone, and identity number live on separate User/StudentProfile
    columns, so table-local uniqueness constraints cannot prevent a race
    between the two tables. A transaction-scoped advisory lock keeps the
    low-volume identity mutation paths in one critical section; the normal
    database constraints remain the final backstop.
    """
    if connection.vendor != "postgresql":
        return
    if not connection.in_atomic_block:
        raise RuntimeError("Identity writes must be locked inside transaction.atomic().")
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [7834129051])


def validate_csv_content(content: bytes) -> list[dict[str, str]]:
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise ValueError(
            _("File size exceeds the maximum of %(size)d MB") % {"size": MAX_FILE_SIZE_BYTES // (1024 * 1024)}
        )
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError(_("CSV file must use UTF-8 encoding.")) from None
    reader = csv.DictReader(io.StringIO(text), strict=True)
    try:
        source_headers = reader.fieldnames
    except csv.Error:
        raise ValueError(_("CSV file is malformed near row 1.")) from None
    if not source_headers:
        raise ValueError(_("CSV file appears to be empty or malformed"))
    if any(header is None or not header.strip() for header in source_headers):
        raise ValueError(_("CSV file contains an empty column name."))
    headers = [h.strip().lower() for h in source_headers]
    if len(headers) != len(set(headers)):
        raise ValueError(_("CSV file contains duplicate column names."))
    reader.fieldnames = headers
    missing = [h for h in REQUIRED_CSV_HEADERS if h not in headers]
    if missing:
        raise ValueError(_("Missing required columns: {columns}").format(columns=", ".join(missing)))
    rows = []
    row_number = 0
    try:
        for row_number, row in enumerate(reader, start=1):
            if row_number > MAX_IMPORT_ROWS:
                raise ValueError(_("CSV exceeds the maximum of %(count)d rows.") % {"count": MAX_IMPORT_ROWS})
            extra_values = row.pop(None, [])
            if any((value or "").strip() for value in extra_values):
                raise ValueError(_("Row %(row)d contains more values than the header.") % {"row": row_number})
            cleaned = {key: (value or "").strip() for key, value in row.items()}
            rows.append(cleaned)
    except csv.Error:
        raise ValueError(_("CSV file is malformed near row %(row)d.") % {"row": max(row_number, 1)}) from None
    return rows


def _load_import_user_scope(*, user_ids: set[int] | None = None) -> tuple[set[int], set[int]]:
    """Return student/staff ids for only the candidate accounts in this run."""
    candidates = User.objects.all()
    if user_ids is not None:
        candidates = candidates.filter(id__in=user_ids)
    student_user_ids = set(candidates.filter(groups__name=GROUP_STUDENT).values_list("id", flat=True))
    staff_user_ids = set(candidates.filter(groups__name__in=STAFF_ROLE_GROUPS).values_list("id", flat=True))
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

    def add_national_candidate(national_id: str, user: User) -> None:
        candidates = national_id_index.setdefault(national_id.upper(), [])
        if all(candidate.id != user.id for candidate in candidates):
            candidates.append(user)

    if national_ids:
        for u in (
            User.objects.annotate(_normalized_national_id=Upper("national_id"))
            .filter(_normalized_national_id__in=national_ids)
            .only("id", "email", "phone", "national_id")
        ):
            add_national_candidate(u._normalized_national_id, u)
        for profile in (
            StudentProfile.objects.annotate(_normalized_national_id=Upper("national_id"))
            .filter(_normalized_national_id__in=national_ids)
            .select_related("user")
            .only(
                "id",
                "user_id",
                "national_id",
                "user__id",
                "user__email",
                "user__phone",
                "user__national_id",
            )
        ):
            add_national_candidate(profile._normalized_national_id, profile.user)
    phone_index: dict[str, list[User]] = {}

    def add_phone_candidate(phone: str, user: User) -> None:
        candidates = phone_index.setdefault(phone, [])
        if all(candidate.id != user.id for candidate in candidates):
            candidates.append(user)

    if phones:
        for u in User.objects.filter(phone__in=phones).only("id", "email", "phone", "national_id"):
            add_phone_candidate(u.phone, u)
    if phones:
        for profile in (
            StudentProfile.objects.filter(phone__in=phones)
            .select_related("user")
            .only("id", "user_id", "phone", "user__id")
        ):
            add_phone_candidate(profile.phone, profile.user)
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
                return (
                    None,
                    MATCH_METHOD_AMBIGUOUS,
                    str(
                        _(
                            "An identifier belongs to a staff account and cannot be modified by student import."
                        )
                    ),
                )

    if national_id:
        matches = national_id_index.get(national_id, [])
        if len(matches) > 1:
            return (
                None,
                MATCH_METHOD_AMBIGUOUS,
                str(_("An identity value matches more than one account and cannot be resolved safely.")),
            )
        if len(matches) == 1:
            u = matches[0]
            if _is_importable(u):
                candidates[u.id] = u
                sources[u.id] = sources.get(u.id, "") + "+national_id"
            else:
                return (
                    None,
                    MATCH_METHOD_AMBIGUOUS,
                    str(
                        _(
                            "An identifier belongs to a staff account and cannot be modified by student import."
                        )
                    ),
                )

    if phone:
        matches = phone_index.get(phone, [])
        if len(matches) == 1:
            u = matches[0]
            if _is_importable(u):
                candidates[u.id] = u
                sources[u.id] = sources.get(u.id, "") + "+phone"
            else:
                return (
                    None,
                    MATCH_METHOD_AMBIGUOUS,
                    str(
                        _(
                            "An identifier belongs to a staff account and cannot be modified by student import."
                        )
                    ),
                )

    if not candidates:
        return None, MATCH_METHOD_NONE, str(_("No student found matching any identifier."))

    if len(candidates) > 1:
        return (
            None,
            MATCH_METHOD_AMBIGUOUS,
            str(_("The identifiers refer to different accounts and cannot be resolved safely.")),
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
    email_index, national_id_index, phone_index = _load_match_indexes(
        {email} if email else set(),
        {national_id} if national_id else set(),
        {phone} if phone else set(),
    )
    candidate_ids = {
        user.id
        for index in (email_index, national_id_index, phone_index)
        for users in index.values()
        for user in users
    }
    student_user_ids, staff_user_ids = _load_import_user_scope(user_ids=candidate_ids)
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


def _get_country_code(row: dict[str, str], *, default: str | None = None) -> str:
    return normalize_country_code((row.get("country_code", "") or "").strip() or (default or "").strip())


def _parse_row_data(row: dict[str, str], row_number: int) -> dict[str, Any]:
    email = row.get("email", "").strip().lower()
    full_name = row.get("full_name", "").strip()
    if not email:
        return {"status": ROW_STATUS_FAILED, "error_message": str(_("Empty email"))}
    if len(email) > 254:
        return {"status": ROW_STATUS_FAILED, "error_message": str(_("Email is too long."))}
    if not full_name:
        return {"status": ROW_STATUS_FAILED, "error_message": str(_("Empty full name"))}
    raw_national_id = row.get("national_id", "")
    # The account's country may be absent from an update CSV; preview resolves
    # that from the matched profile before validating the national/passport ID.
    national_id = normalize_identity_number("OTHER", raw_national_id) if raw_national_id else ""
    if len(national_id) > 32:
        return {"status": ROW_STATUS_FAILED, "error_message": str(_("Identity number is too long."))}
    phone = normalize_phone(row.get("phone", ""))
    if row.get("phone", "").strip():
        try:
            validate_phone(row["phone"])
        except ValidationError as exc:
            return {"status": ROW_STATUS_FAILED, "error_message": str(exc)}
    gender_choices = {value for value, _label in StudentProfile._meta.get_field("gender").choices}
    if row.get("gender", "").strip() and row["gender"].strip() not in gender_choices:
        return {"status": ROW_STATUS_FAILED, "error_message": str(_("Invalid gender value."))}
    education_choices = {value for value, _label in StudentProfile._meta.get_field("education").choices}
    if row.get("education", "").strip() and row["education"].strip() not in education_choices:
        return {"status": ROW_STATUS_FAILED, "error_message": str(_("Invalid education value."))}
    if row.get("date_of_birth", "").strip() and parse_date(row["date_of_birth"]) is None:
        return {"status": ROW_STATUS_FAILED, "error_message": str(_("Invalid date of birth."))}
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


def _import_row_error(row_number: int, error: str) -> str:
    return str(_("Row %(row)d: %(error)s") % {"row": row_number, "error": error})


@transaction.atomic
def preview_upload(upload_id: int) -> UserUpload:
    upload = UserUpload.objects.select_for_update().get(id=upload_id)
    if upload.status in (UPLOAD_STATUS_PREVIEWED, UPLOAD_STATUS_FAILED, UPLOAD_STATUS_CONFIRMED):
        return upload
    if upload.status != UPLOAD_STATUS_PENDING:
        raise ValueError(_("Upload is not ready for preview."))
    # Keep the candidate indexes and their stored fingerprint from observing
    # different sides of a concurrent identity edit.
    lock_identity_writes()
    errors = [
        _import_row_error(row_number, error_message)
        for row_number, error_message in upload.rows.filter(
            status=ROW_STATUS_FAILED,
        )
        .exclude(error_message="")
        .order_by("row_number")
        .values_list("row_number", "error_message")
    ]

    rows = list(upload.rows.filter(status=ROW_STATUS_PARSED).order_by("row_number"))
    email_index, national_id_index, phone_index = _load_match_indexes(
        {r.normalized_email.lower() for r in rows if r.normalized_email},
        {r.normalized_national_id for r in rows if r.normalized_national_id},
        {r.normalized_phone for r in rows if r.normalized_phone},
    )
    candidate_ids = {
        user.id
        for index in (email_index, national_id_index, phone_index)
        for users in index.values()
        for user in users
    }
    profiles_by_user = {
        profile.user_id: profile
        for profile in StudentProfile.objects.filter(user_id__in=candidate_ids).only(
            "user_id",
            "phone",
            "national_id",
            "country_code",
        )
    }
    student_user_ids, staff_user_ids = _load_import_user_scope(user_ids=candidate_ids)
    seen_emails: dict[str, int] = {}
    seen_national_ids: dict[str, int] = {}
    seen_phones: dict[str, int] = {}
    row_by_email: dict[str, UserUploadRow] = {}
    row_by_nid: dict[str, UserUploadRow] = {}
    row_by_phone: dict[str, UserUploadRow] = {}
    row_by_matched_user: dict[int, UserUploadRow] = {}

    for row in rows:
        email = row.normalized_email.lower() if row.normalized_email else ""
        nid = row.normalized_national_id
        phone = row.normalized_phone
        if email and email in seen_emails:
            first_row = row_by_email[email]
            first_row.status = ROW_STATUS_AMBIGUOUS
            first_row.error_message = str(
                _("Duplicate email entries in rows %(first)d and %(second)d.")
                % {"first": first_row.row_number, "second": row.row_number}
            )
            errors.append(_import_row_error(first_row.row_number, first_row.error_message))

            row.status = ROW_STATUS_AMBIGUOUS
            row.error_message = first_row.error_message
            errors.append(_import_row_error(row.row_number, row.error_message))
            continue
        if nid and nid in seen_national_ids:
            first_row = row_by_nid[nid]
            first_row.status = ROW_STATUS_AMBIGUOUS
            first_row.error_message = str(
                _("Duplicate identity values in rows %(first)d and %(second)d.")
                % {"first": first_row.row_number, "second": row.row_number}
            )
            errors.append(_import_row_error(first_row.row_number, first_row.error_message))

            row.status = ROW_STATUS_AMBIGUOUS
            row.error_message = first_row.error_message
            errors.append(_import_row_error(row.row_number, row.error_message))
            continue
        if phone and phone in seen_phones:
            first_row = row_by_phone[phone]
            first_row.status = ROW_STATUS_AMBIGUOUS
            first_row.error_message = str(
                _("Duplicate phone numbers in rows %(first)d and %(second)d.")
                % {"first": first_row.row_number, "second": row.row_number}
            )
            errors.append(_import_row_error(first_row.row_number, first_row.error_message))

            row.status = ROW_STATUS_AMBIGUOUS
            row.error_message = first_row.error_message
            errors.append(_import_row_error(row.row_number, row.error_message))
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
            profile = profiles_by_user.get(user.id)
            validation_error = _validate_import_profile_row(
                data,
                require_identity=profile is None,
                current_country_code=profile.country_code if profile else user.country_code,
            )
            if validation_error:
                row.status = ROW_STATUS_FAILED
                row.error_message = validation_error
                errors.append(_import_row_error(row.row_number, validation_error))
                continue
            previous_row = row_by_matched_user.get(user.pk)
            if previous_row is not None:
                message = str(
                    _("Multiple CSV rows identify the same account (rows %(first)d and %(second)d).")
                    % {"first": previous_row.row_number, "second": row.row_number}
                )
                previous_row.status = ROW_STATUS_AMBIGUOUS
                previous_row.matched_user = None
                previous_row.match_method = MATCH_METHOD_AMBIGUOUS
                previous_row.error_message = message
                row.status = ROW_STATUS_AMBIGUOUS
                row.match_method = MATCH_METHOD_AMBIGUOUS
                row.error_message = message
                errors.extend(
                    (
                        _import_row_error(previous_row.row_number, message),
                        _import_row_error(row.row_number, message),
                    )
                )
                continue
            row.matched_user = user
            row.match_method = method
            row.status = ROW_STATUS_MATCHED
            row_by_matched_user[user.pk] = row
        else:
            if method == MATCH_METHOD_NONE:
                validation_error = _validate_import_profile_row(data, require_identity=True)
                if validation_error:
                    row.status = ROW_STATUS_FAILED
                    row.error_message = validation_error
                else:
                    row.status = ROW_STATUS_UNMATCHED
            else:
                row.status = ROW_STATUS_FAILED if method != MATCH_METHOD_AMBIGUOUS else ROW_STATUS_AMBIGUOUS
                row.error_message = error
            if row.error_message:
                errors.append(_import_row_error(row.row_number, row.error_message))

    for row in rows:
        if row.matched_user_id:
            row.preview_identity = _identity_fingerprint(
                row.matched_user,
                profiles_by_user.get(row.matched_user_id),
            )

    now = timezone.now()
    for row in rows:
        row.updated_at = now
    upload.rows.bulk_update(
        rows,
        ["matched_user", "match_method", "preview_identity", "status", "error_message", "updated_at"],
    )

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
        section=SECTION_ACCOUNTS,
        target_label=upload.original_filename,
        metadata={"matched": matched, "unmatched": unmatched, "failed": failed, "errors": errors},
    )
    return upload


def _build_student_profile_data(
    row_raw: dict[str, str], *, default_country_code: str | None = None
) -> dict[str, Any]:
    country_code = _get_country_code(row_raw, default=default_country_code)
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


def _identity_fingerprint(user, profile=None) -> dict[str, str]:
    """Return a non-reversible snapshot of all identity fields used by import."""
    values = {
        "email": (user.email or "").strip().lower(),
        "user_phone": normalize_phone(user.phone),
        "user_national_id": user.national_id or "",
        "user_country_code": normalize_country_code(user.country_code),
        "profile_phone": normalize_phone(profile.phone) if profile else "",
        "profile_national_id": profile.national_id if profile else "",
        "profile_country_code": normalize_country_code(profile.country_code) if profile else "",
    }
    return {
        key: salted_hmac(
            "accounts.user_upload.preview_identity.v1",
            f"{key}:{value}",
            algorithm="sha256",
        ).hexdigest()
        for key, value in values.items()
    }


def _validate_import_profile_row(
    row: dict[str, str], *, require_identity: bool, current_country_code: str | None = None
) -> str | None:
    """Validate profile values before preview and again before confirmation."""
    email = row.get("email", "").strip().lower()
    if not email:
        return str(_("Email is required."))
    try:
        validate_email(email)
    except ValidationError as exc:
        return str(exc)
    if len(email) > 254:
        return str(_("Email is too long."))
    full_name = row.get("full_name", "").strip()
    if not full_name:
        return str(_("Full name is required."))
    if len(full_name) > 150:
        return str(_("Full name is too long."))
    raw_country_code = row.get("country_code", "").strip()
    declared_country_codes = {value.upper() for value, _label in COUNTRY_CHOICES}
    if (
        raw_country_code
        and len(raw_country_code) > 2
        and raw_country_code.upper() not in declared_country_codes
    ):
        return str(_("Country code is too long."))
    phone = row.get("phone", "").strip()
    raw_national_id = row.get("national_id", "").strip()
    country_code = _get_country_code(row, default=current_country_code)
    supported_country_codes = {normalize_country_code(value) for value, _label in COUNTRY_CHOICES}
    if raw_country_code and country_code not in supported_country_codes:
        return str(_("Invalid country code."))
    if (
        current_country_code
        and row.get("country_code", "").strip()
        and country_code != normalize_country_code(current_country_code)
        and not raw_national_id
    ):
        return str(_("An identity number is required when changing the country."))
    if require_identity and not phone:
        return str(_("Phone number is required for a new student."))
    if require_identity and not raw_national_id:
        return str(_("National ID is required for a new student."))
    if phone:
        try:
            validate_phone(phone)
        except ValidationError as exc:
            return str(exc)
    if raw_national_id:
        try:
            validate_identity_number(country_code, raw_national_id)
        except ValidationError as exc:
            return str(exc)
        if len(normalize_identity_number(country_code, raw_national_id)) > 32:
            return str(_("Identity number is too long."))
    field_limits = {
        "address": 255,
        "specialization": 120,
        "employer": 120,
        "current_job": 120,
        "church": 120,
        "priest_name": 120,
        "priest_contact": 120,
    }
    for field_name, max_length in field_limits.items():
        if len(row.get(field_name, "").strip()) > max_length:
            label = str(StudentProfile._meta.get_field(field_name).verbose_name)
            return str(_("%(field)s is too long.") % {"field": label})
    gender = row.get("gender", "").strip()
    gender_choices = {value for value, _label in StudentProfile._meta.get_field("gender").choices}
    if gender and gender not in gender_choices:
        return str(_("Invalid gender value."))
    education = row.get("education", "").strip()
    education_choices = {value for value, _label in StudentProfile._meta.get_field("education").choices}
    if education and education not in education_choices:
        return str(_("Invalid education value."))
    date_of_birth = row.get("date_of_birth", "").strip()
    if date_of_birth and parse_date(date_of_birth) is None:
        return str(_("Invalid date of birth."))
    if require_identity:
        missing = [
            str(StudentProfile._meta.get_field(field).verbose_name)
            for field in ("date_of_birth", "gender", "address", "education")
            if not row.get(field, "").strip()
        ]
        if missing:
            return str(_("Missing required profile fields: %(fields)s") % {"fields": ", ".join(missing)})
    return None


def _validate_import_identity_conflicts(
    row: dict[str, str],
    *,
    user_id: int | None = None,
    profile_id: int | None = None,
    current_country_code: str | None = None,
) -> str | None:
    """Check current database identity conflicts while the import lock is held."""
    email = row.get("email", "").strip().lower()
    if email and User.objects.exclude(pk=user_id).filter(email__iexact=email).exists():
        return str(_("Email is already in use."))
    try:
        validate_unique_identity_values(
            country_code=_get_country_code(row, default=current_country_code),
            phone=row.get("phone", "").strip(),
            identity_number=row.get("national_id", "").strip(),
            user_id=user_id,
            profile_id=profile_id,
        )
    except ValidationError as exc:
        return str(exc)
    return None


@transaction.atomic
def confirm_upload(upload_id: int, confirmed_by) -> UserUpload:
    upload = UserUpload.objects.select_for_update().get(id=upload_id)
    if upload.status == UPLOAD_STATUS_CONFIRMED:
        return upload
    if upload.status != UPLOAD_STATUS_PREVIEWED:
        raise ValueError(_("Upload is not in previewed state"))
    if upload.matched_rows == 0 and upload.unmatched_rows == 0:
        raise ValueError(_("No rows matched or unmatched; nothing to import"))

    student_group = Group.objects.get(name=GROUP_STUDENT)
    errors: list[str] = []

    rows = list(
        upload.rows.select_for_update()
        .filter(status__in=[ROW_STATUS_MATCHED, ROW_STATUS_UNMATCHED])
        .order_by("row_number")
    )
    if not rows:
        raise ValueError(_("No rows matched or unmatched; nothing to import"))

    update_rows = [row for row in rows if row.matched_user_id]
    create_rows = [row for row in rows if not row.matched_user_id]
    created = len(create_rows)
    updated = len(update_rows)

    try:
        lock_identity_writes()
        matched_ids = {row.matched_user_id for row in update_rows if row.matched_user_id}
        student_user_ids, staff_user_ids = _load_import_user_scope(user_ids=matched_ids)
        if not matched_ids.issubset(student_user_ids) or matched_ids.intersection(staff_user_ids):
            raise ValueError(
                _(
                    "The preview is stale because one or more matched accounts are no longer eligible student accounts."
                )
            )
        # Matched users: fetch once and apply the CSV overrides.
        matched_users = {
            user.id: user for user in User.objects.select_for_update().filter(id__in=matched_ids)
        }
        matched_profiles = {
            profile.user_id: profile
            for profile in StudentProfile.objects.select_for_update().filter(user_id__in=matched_ids)
        }
        for row in update_rows:
            user = matched_users.get(row.matched_user_id)
            profile = matched_profiles.get(row.matched_user_id)
            if user is None:
                raise ValueError(_("The preview is stale. Please upload the file again."))
            if not row.preview_identity:
                raise ValueError(
                    _(
                        "This preview is too old to confirm safely. Upload the CSV again and review a fresh preview."
                    )
                )
            if row.preview_identity != _identity_fingerprint(user, profile):
                raise ValueError(_("The preview is stale because a matched account changed."))
        validation_errors = []
        for row in rows:
            matched_user = matched_users.get(row.matched_user_id) if row.matched_user_id else None
            profile = matched_profiles.get(row.matched_user_id) if row.matched_user_id else None
            current_country_code = (
                profile.country_code if profile else (matched_user.country_code if matched_user else None)
            )
            validation_error = _validate_import_profile_row(
                row.raw_data,
                require_identity=not row.matched_user_id or profile is None,
                current_country_code=current_country_code,
            )
            if validation_error:
                validation_errors.append(_import_row_error(row.row_number, validation_error))
                continue
            identity_error = _validate_import_identity_conflicts(
                row.raw_data,
                user_id=row.matched_user_id,
                profile_id=profile.pk if profile else None,
                current_country_code=current_country_code,
            )
            if identity_error:
                validation_errors.append(_import_row_error(row.row_number, identity_error))
        if validation_errors:
            raise ValueError(
                _("Import validation failed:\n%(errors)s") % {"errors": "\n".join(validation_errors)}
            )

        matched_profile_data = {}
        for row in update_rows:
            user = matched_users.get(row.matched_user_id)
            if user is None:
                continue
            profile = matched_profiles.get(user.id)
            matched_profile_data[user.id] = _build_student_profile_data(
                row.raw_data,
                default_country_code=profile.country_code if profile else user.country_code,
            )

        new_profile_data_by_row = {
            row.row_number: _build_student_profile_data(row.raw_data) for row in create_rows
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
            profile = matched_profiles.get(user.id)
            profile_data = matched_profile_data.get(user.id, {})
            user.full_name = profile_data.get("full_name", user.full_name)
            user.national_id = profile_data.get("national_id") or (
                profile.national_id if profile else user.national_id
            )
            user.country_code = profile_data.get(
                "country_code",
                profile.country_code if profile else user.country_code,
            )
            user.phone = ""
            if not (user.full_name and user.email):
                user.full_name = user.full_name or user.email
            user.full_name_ar = user.full_name_ar or user.full_name
            user.updated_at = timezone.now()
            users_to_update.append(user)
        if users_to_update:
            User.objects.bulk_update(
                users_to_update,
                ["full_name", "full_name_ar", "phone", "national_id", "country_code", "updated_at"],
            )

        # New users: create in bulk, then attach the Student group in bulk.
        new_users = []
        for row in create_rows:
            profile_data = new_profile_data_by_row[row.row_number]
            full_name = profile_data.get("full_name", row.raw_data.get("full_name", ""))
            user = User(
                email=row.normalized_email,
                full_name=full_name,
                full_name_ar=full_name,
                national_id=profile_data.get("national_id", row.normalized_national_id),
                country_code=profile_data.get("country_code", "EG"),
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
            profile_data = matched_profile_data.get(user.id, {})
            if profile_data:
                merged_profile_data.setdefault(user.id, {}).update(profile_data)
        for row in create_rows:
            profile_data = new_profile_data_by_row[row.row_number]
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
    except ValueError:
        raise
    except Exception:  # noqa: BLE001
        raise ValueError(_("Import failed for {count} row(s)").format(count=len(rows))) from None

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
        section=SECTION_ACCOUNTS,
        target_label=upload.original_filename,
        metadata={"created": created, "updated": updated, "errors": errors},
    )
    return upload


def send_activation_email(user, *, idempotency_key: str | None = None, sent_by=None) -> object | None:
    """Queue the account activation email.

    Returns the queued ``EmailMessage`` row, or ``None`` when no active/enabled
    activation template exists (the caller is expected to surface that).
    """
    uidb64, token = generate_activation_token(user)
    activation_url = settings.SITE_URL + reverse(
        "accounts:activate", kwargs={"uidb64": uidb64, "token": token}
    )
    full_name = getattr(user, "full_name", "") or ""
    # The initial password is never delivered by email (S2-24); the activation
    # link is the one-time token and the password is set during activation.
    return queue_templated_email(
        TEMPLATE_ACTIVATION,
        user.email,
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
def delete_user_account(user, actor, *, require_student: bool = False) -> None:
    """Delete an unreferenced account without deleting durable history."""
    lock_identity_writes()
    try:
        locked_user = User.objects.select_for_update().get(pk=user.pk)
    except User.DoesNotExist:
        raise ValueError(_("This account no longer exists.")) from None
    if locked_user.pk == getattr(actor, "pk", None):
        raise ValueError(_("You cannot delete your own account."))
    # Academic records commonly reference StudentProfile rather than User;
    # lock that owner row so a concurrent FK insert cannot slip between the
    # history check and a cascading account deletion.
    profile = StudentProfile.objects.select_for_update().filter(user=locked_user).first()
    if require_student and profile is None:
        raise ValueError(_("This account has no student profile."))
    if (
        profile is not None and _has_cascade_or_protected_records(profile)
    ) or _has_cascade_or_protected_records(locked_user, excluded_models=(StudentProfile,)):
        raise ValueError(_("This account cannot be deleted because historical records are linked to it."))

    user_id = locked_user.id
    role = locked_user.primary_role
    is_active = locked_user.is_active
    user_label = str(locked_user)
    try:
        with transaction.atomic():
            locked_user.delete()
    except IntegrityError:
        raise ValueError(
            _("This account cannot be deleted because linked records changed during deletion.")
        ) from None
    record_audit_event(
        actor=actor,
        action=ACTION_USER_DELETED,
        target_type="user",
        target_id=str(user_id),
        section=SECTION_ACCOUNTS,
        target_label=user_label,
        subject_type="user",
        subject_id=str(user_id),
        subject_label=user_label,
        metadata={
            "student_account": profile is not None,
            "role": role,
            "active": is_active,
        },
    )


def delete_student_account(user, actor) -> None:
    """Delete an unused student account without deleting historical records."""
    delete_user_account(user, actor, require_student=True)
