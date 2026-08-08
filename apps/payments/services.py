from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.academics.models import CourseEnrollment, DiplomaEnrollment
from apps.applications.services import activate_application_if_ready
from apps.audit.constants import ACTION_PAYMENT_RECORD_UPDATED
from apps.audit.services import record_audit_event
from apps.core.permissions import validate_score
from apps.students.identity import normalize_identity_number
from apps.students.models import StudentProfile

from .constants import (
    CURRENCY_EGP,
    MATCH_METHOD_AMBIGUOUS,
    MATCH_METHOD_EMAIL,
    MATCH_METHOD_NATIONAL_ID,
    MATCH_METHOD_NONE,
    MATCH_METHOD_PHONE,
    PAYMENT_STATUS_PAID,
    PAYMENT_STATUS_UNPAID,
    REQUIRED_CSV_HEADERS,
    ROW_STATUS_AMBIGUOUS,
    ROW_STATUS_CREATED,
    ROW_STATUS_FAILED,
    ROW_STATUS_MATCHED,
    ROW_STATUS_PARSED,
    ROW_STATUS_UPDATED,
    UPLOAD_STATUS_CONFIRMED,
    UPLOAD_STATUS_FAILED,
    UPLOAD_STATUS_PENDING,
    UPLOAD_STATUS_PREVIEWED,
)
from .models import PaymentRecord, PaymentUpload, PaymentUploadRow

logger = logging.getLogger(__name__)


# ponytail: delegate to country-aware normalize_identity_number, default EG for CSV imports
def normalize_national_id(value: str) -> str:
    """Normalize identity value for CSV matching (EG default)."""
    return normalize_identity_number("EG", value)


def normalize_national_id_for_matching(value: str) -> list[str]:
    """Return normalized variants to try when matching.

    Uses country-aware normalize_identity_number (default EG).
    For passport IDs, an uppercased variant is also returned.
    """
    stripped = normalize_national_id(value)
    variants = [stripped]
    if any(c.isalpha() for c in stripped):
        variants.append(normalize_identity_number("EG", stripped.upper()))
    return variants


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


def match_student(row: dict[str, str]) -> tuple[StudentProfile | None, str, str]:
    identity_variants = normalize_national_id_for_matching(row.get("national_id", ""))
    email = row.get("email", "").strip().lower()
    phone = normalize_phone(row.get("phone", ""))
    candidates: dict[int, StudentProfile] = {}
    sources: dict[int, str] = {}

    if identity_variants:
        qs = StudentProfile.objects.filter(national_id=identity_variants[0])
        if qs.count() == 1:
            s = qs.first()
            candidates[s.id] = s
            sources[s.id] = "national_id"
        elif qs.count() > 1:
            return None, MATCH_METHOD_AMBIGUOUS, f"Multiple students match national ID {identity_variants[0]}"
        elif len(identity_variants) > 1:
            qs = StudentProfile.objects.filter(national_id=identity_variants[1])
            if qs.count() == 1:
                s = qs.first()
                candidates[s.id] = s
                sources[s.id] = "national_id"
            elif qs.count() > 1:
                return None, MATCH_METHOD_AMBIGUOUS, f"Multiple students match national ID {identity_variants[0]}"

    if email:
        qs = StudentProfile.objects.filter(user__email__iexact=email)
        if qs.count() == 1:
            s = qs.first()
            candidates[s.id] = s
            sources[s.id] = sources.get(s.id, "") + "+email"
        elif qs.count() > 1:
            return None, MATCH_METHOD_AMBIGUOUS, f"Multiple students match email {email}"

    if phone:
        qs = StudentProfile.objects.filter(phone=phone)
        if qs.count() == 1:
            s = qs.first()
            candidates[s.id] = s
            sources[s.id] = sources.get(s.id, "") + "+phone"
        elif qs.count() > 1:
            return None, MATCH_METHOD_AMBIGUOUS, f"Multiple students match phone {phone}"

    if not candidates:
        return None, MATCH_METHOD_NONE, "No student found matching any identifier"

    if len(candidates) > 1:
        return None, MATCH_METHOD_AMBIGUOUS, (
            f"Conflicting identifiers match {len(candidates)} different students"
        )

    match = next(iter(candidates.values()))
    method_str = sources.get(match.id, "")
    if "national_id" in method_str:
        method = MATCH_METHOD_NATIONAL_ID
    elif "email" in method_str:
        method = MATCH_METHOD_EMAIL
    elif "phone" in method_str:
        method = MATCH_METHOD_PHONE
    else:
        method = MATCH_METHOD_NONE
    return match, method, ""


def _lookup_application(data: dict, student: StudentProfile) -> tuple:
    """Return (application, error)."""
    app_id = data.get("application_id", "").strip()
    if not app_id:
        return None, ""
    try:
        from apps.applications.models import ProgramApplication
        app = ProgramApplication.objects.filter(id=int(app_id), student=student).first()
        if app:
            return app, ""
        return None, "application_id does not match this student"
    except (ValueError, TypeError):
        return None, "invalid application_id"


def _lookup_diploma_enrollment(data: dict, student: StudentProfile) -> DiplomaEnrollment | None:
    enroll_id = data.get("diploma_enrollment_id", "").strip()
    if not enroll_id:
        return None
    try:
        return DiplomaEnrollment.objects.select_related("cohort__diploma").filter(id=int(enroll_id), student=student).first()
    except (ValueError, TypeError):
        return None


def _lookup_course_enrollment(data: dict, student: StudentProfile) -> CourseEnrollment | None:
    enroll_id = data.get("course_enrollment_id", "").strip()
    if not enroll_id:
        return None
    try:
        return CourseEnrollment.objects.select_related("course_version__course").filter(id=int(enroll_id), student=student).first()
    except (ValueError, TypeError):
        return None


def _build_target_snapshot(
    row,
    diploma_enrollment: DiplomaEnrollment | None,
    course_enrollment: CourseEnrollment | None,
) -> tuple[str, dict]:
    if diploma_enrollment:
        enroll = diploma_enrollment
        return "diploma", {
            "cohort_name": enroll.cohort.name if enroll.cohort else "",
            "diploma_title": enroll.cohort.diploma.title if enroll.cohort and enroll.cohort.diploma else "",
        }
    if course_enrollment:
        enroll = course_enrollment
        return "course", {
            "course_title": enroll.course_version.course.title if enroll.course_version and enroll.course_version.course else "",
            "course_version_name": enroll.course_version.label if enroll.course_version else "",
        }
    return "", {}


def preview_upload(upload_id: int) -> PaymentUpload:
    upload = PaymentUpload.objects.get(id=upload_id)
    errors: list[str] = []
    all_parsed = True
    matched = unmatched = failed = 0

    for row in upload.rows.filter(status=ROW_STATUS_PARSED):
        data = row.raw_data
        student, method, error = match_student(data)
        if student:
            row.matched_student = student
            row.match_method = method
            row.status = ROW_STATUS_MATCHED
            app, app_error = _lookup_application(data, student)
            if app_error:
                row.status = ROW_STATUS_FAILED
                row.error_message = app_error
                errors.append(f"Row {row.row_number}: {app_error}")
                row.save(update_fields=["status", "error_message"])
                failed += 1
                continue
            row.matched_application = app
            row.matched_diploma_enrollment = _lookup_diploma_enrollment(data, student)
            row.matched_course_enrollment = _lookup_course_enrollment(data, student)
            matched += 1
        else:
            row.status = ROW_STATUS_FAILED if method != MATCH_METHOD_AMBIGUOUS else ROW_STATUS_AMBIGUOUS
            row.error_message = error
            errors.append(f"Row {row.row_number}: {error}")
            if method == MATCH_METHOD_NONE:
                unmatched += 1
            else:
                failed += 1
            all_parsed = False
        row.save(update_fields=["matched_student", "match_method", "status", "error_message",
                                "matched_application", "matched_diploma_enrollment", "matched_course_enrollment"])

    upload.matched_rows = matched
    upload.unmatched_rows = unmatched
    upload.failed_rows = failed
    upload.status = UPLOAD_STATUS_PREVIEWED if all_parsed else UPLOAD_STATUS_FAILED
    upload.error_summary = "\n".join(errors) if errors else ""
    upload.save(update_fields=["matched_rows", "unmatched_rows", "failed_rows", "status", "error_summary"])
    return upload


def _find_existing_payment(row) -> PaymentRecord | None:
    if row.external_reference:
        return PaymentRecord.objects.filter(
            student=row.matched_student,
            external_reference=row.external_reference,
        ).first()
    return PaymentRecord.objects.filter(
        student=row.matched_student,
        amount=row.amount,
        paid_at=row.paid_at,
        currency=row.currency,
    ).first()


@transaction.atomic
def confirm_upload(upload_id: int, confirmed_by) -> PaymentUpload:
    """Apply a fully validated payment CSV atomically (P0).

    Two-phase: (1) every matched row is re-validated against the current
    database and a write plan is built, with no writes and exact row
    diagnostics; (2) only if every row is valid are all rows applied inside
    one transaction. Any row failure marks the upload failed with zero
    partial writes, per the atomic-per-file payment CSV rule.
    """
    upload = PaymentUpload.objects.select_for_update().get(id=upload_id)
    if upload.status != UPLOAD_STATUS_PREVIEWED:
        raise ValueError(_("Upload is not in previewed state"))
    if upload.matched_rows == 0:
        raise ValueError(_("No rows matched; nothing to import"))

    rows = list(upload.rows.select_for_update().filter(status=ROW_STATUS_MATCHED).select_related("matched_student"))
    errors: list[str] = []
    plans: list[tuple[PaymentUploadRow, StudentProfile, Any, DiplomaEnrollment | None, CourseEnrollment | None, PaymentRecord | None]] = []
    for row in rows:
        try:
            student, method, error = match_student(row.raw_data)
            if student is None or student.id != row.matched_student_id:
                raise ValueError(error or _("No student found matching the row identifiers."))
            app, app_error = _lookup_application(row.raw_data, student)
            if app_error:
                raise ValueError(app_error)
            diploma_enrollment = _lookup_diploma_enrollment(row.raw_data, student)
            course_enrollment = _lookup_course_enrollment(row.raw_data, student)
            existing = _find_existing_payment(row)
            plans.append((row, student, app, diploma_enrollment, course_enrollment, existing))
        except Exception as exc:
            errors.append(f"Row {row.row_number}: {exc}")

    if errors:
        upload.failed_rows = len(errors)
        upload.status = UPLOAD_STATUS_FAILED
        upload.error_summary = "\n".join(errors)
        upload.completed_at = timezone.now()
        upload.save(update_fields=["failed_rows", "status", "error_summary", "completed_at", "updated_at"])
        return upload

    try:
        _apply_payment_rows(upload, plans, confirmed_by)
    except Exception as exc:
        upload.failed_rows = len(rows)
        upload.status = UPLOAD_STATUS_FAILED
        upload.error_summary = str(exc)
        upload.completed_at = timezone.now()
        upload.save(update_fields=["failed_rows", "status", "error_summary", "completed_at", "updated_at"])
        return upload

    # Attempt activation for application-linked rows (best-effort, after the
    # import itself committed; activation failures never roll back the file).
    for row in upload.rows.filter(status__in=[ROW_STATUS_CREATED, ROW_STATUS_UPDATED]):
        if row.matched_application:
            try:
                activate_application_if_ready(row.matched_application, actor=confirmed_by)
            except Exception as e:
                logger.warning("Activation failed for application %s: %s", row.matched_application.pk, e)

    upload.created_rows = sum(1 for _row, *_ in plans if _row.status == ROW_STATUS_CREATED)
    upload.updated_rows = sum(1 for _row, *_ in plans if _row.status == ROW_STATUS_UPDATED)
    upload.status = UPLOAD_STATUS_CONFIRMED
    upload.completed_at = timezone.now()
    upload.save(
        update_fields=[
            "created_rows", "updated_rows", "failed_rows", "status",
            "error_summary", "completed_at",
        ]
    )
    return upload


@transaction.atomic
def _apply_payment_rows(upload: PaymentUpload, plans, confirmed_by) -> None:
    """Write every validated row inside one transaction.

    Any exception aborts the whole transaction so the file is never partially
    applied. The student row is locked per plan to serialize concurrent
    imports against the same student.
    """
    for row, student, app, diploma_enrollment, course_enrollment, existing in plans:
        StudentProfile.objects.filter(pk=student.pk).select_for_update().exists()
        existing = _find_existing_payment(row)
        target_type, target_snapshot = _build_target_snapshot(
            row, diploma_enrollment, course_enrollment,
        )
        if existing:
            existing.amount = row.amount
            existing.paid_at = row.paid_at
            existing.status = PAYMENT_STATUS_PAID
            existing.notes = row.raw_data.get("notes", "")
            existing.imported_by = confirmed_by
            existing.application = app
            existing.diploma_enrollment = diploma_enrollment
            existing.course_enrollment = course_enrollment
            if target_type:
                existing.target_type = target_type
                existing.target_snapshot = target_snapshot
            target_ref = row.raw_data.get("target_ref", "")
            if target_ref:
                existing.target_ref = target_ref
            existing.save(update_fields=["amount", "paid_at", "status", "notes", "imported_by",
                                          "application", "diploma_enrollment", "course_enrollment",
                                          "target_type", "target_snapshot", "target_ref", "updated_at"])
            row.status = ROW_STATUS_UPDATED
        else:
            PaymentRecord.objects.create(
                student=student,
                upload_row=row,
                external_reference=row.external_reference or "",
                amount=row.amount,
                currency=row.currency,
                status=PAYMENT_STATUS_PAID,
                paid_at=row.paid_at,
                notes=row.raw_data.get("notes", ""),
                imported_by=confirmed_by,
                application=app,
                diploma_enrollment=diploma_enrollment,
                course_enrollment=course_enrollment,
                target_type=row.raw_data.get("target_type", target_type),
                target_ref=row.raw_data.get("target_ref", ""),
                target_snapshot=target_snapshot,
            )
            row.status = ROW_STATUS_CREATED
        row.save(update_fields=["status"])


def parse_row_data(row: dict[str, str], row_number: int) -> dict[str, Any]:
    amount_raw = row.get("amount", "")
    try:
        amount = Decimal(amount_raw) if amount_raw else None
    except InvalidOperation:
        return {"status": ROW_STATUS_FAILED, "error_message": str(_("Invalid amount: %(amount)s") % {"amount": amount_raw})}
    paid_at = parse_date(row.get("payment_date", ""))
    if not paid_at and row.get("payment_date", "").strip():
        return {"status": ROW_STATUS_FAILED, "error_message": str(_("Invalid date: %(date)s") % {"date": row.get("payment_date", "")})}
    national_id = normalize_national_id(row.get("national_id", ""))
    if not national_id:
        return {"status": ROW_STATUS_FAILED, "error_message": str(_("Empty national ID"))}
    email = row.get("email", "").strip().lower()
    phone = normalize_phone(row.get("phone", ""))
    return {
        "raw_data": row,
        "normalized_national_id": national_id,
        "normalized_email": email,
        "normalized_phone": phone,
        "amount": amount,
        "currency": CURRENCY_EGP,
        "paid_at": paid_at,
        "external_reference": row.get("external_reference", ""),
        "status": ROW_STATUS_PARSED,
        "error_message": "",
    }


@transaction.atomic
def create_upload(original_filename: str, content: bytes, uploaded_by) -> PaymentUpload:
    rows = validate_csv_content(content)
    fhash = hashlib.sha256(content).hexdigest()
    upload = PaymentUpload.objects.create(
        uploaded_by=uploaded_by,
        original_filename=original_filename,
        file_hash=fhash,
        status=UPLOAD_STATUS_PENDING,
        started_at=timezone.now(),
        total_rows=len(rows),
    )
    for i, row in enumerate(rows, start=1):
        parsed = parse_row_data(row, i)
        PaymentUploadRow.objects.create(
            upload=upload,
            row_number=i,
            **parsed,
        )
    return upload


# ---------------------------------------------------------------------------
# Per-enrollment payment toggle (Phase 8)
# ---------------------------------------------------------------------------


@transaction.atomic
def mark_enrollment_paid(
    student: StudentProfile,
    enrollment_type: str,
    enrollment_id: int,
    actor,
    amount: Decimal | int = 0,
) -> PaymentRecord:
    """Create a paid PaymentRecord linked to the given enrollment.

    Snapshots the target enrollment name so financial history survives
    target deletion.
    """
    paid_amount = validate_score(amount)

    kwargs: dict = {
        "student": student,
        "amount": paid_amount,
        "currency": CURRENCY_EGP,
        "status": PAYMENT_STATUS_PAID,
        "paid_at": timezone.now(),
        "imported_by": actor,
        "notes": f"Manual payment by {actor.email}",
        "target_type": enrollment_type,
    }
    if enrollment_type == "diploma":
        enrollment = DiplomaEnrollment.objects.select_for_update().get(pk=enrollment_id, student=student)
        kwargs["diploma_enrollment"] = enrollment
        kwargs["target_ref"] = str(enrollment.id)
        kwargs["target_snapshot"] = {
            "cohort_name": enrollment.cohort.name if enrollment.cohort else "",
            "diploma_title": enrollment.cohort.diploma.title if enrollment.cohort and enrollment.cohort.diploma else "",
        }
    elif enrollment_type == "course":
        enrollment = CourseEnrollment.objects.select_for_update().get(pk=enrollment_id, student=student)
        kwargs["course_enrollment"] = enrollment
        kwargs["target_ref"] = str(enrollment.id)
        kwargs["target_snapshot"] = {
            "course_title": enrollment.course_version.course.title if enrollment.course_version and enrollment.course_version.course else "",
            "course_version_name": enrollment.course_version.label if enrollment.course_version else "",
        }
    else:
        raise ValueError(_("Invalid enrollment type: {t}").format(t=enrollment_type))
    # Guard: duplicate payment (exclude soft-deleted records)
    dup_filter = {"student": student, "status": PAYMENT_STATUS_PAID, "deleted_at__isnull": True}
    if enrollment_type == "diploma":
        dup_filter["diploma_enrollment_id"] = enrollment_id
    else:
        dup_filter["course_enrollment_id"] = enrollment_id
    if PaymentRecord.objects.filter(**dup_filter).exists():
        raise ValueError(_("Payment already recorded for this enrollment."))
    return PaymentRecord.objects.create(**kwargs)


@transaction.atomic
def mark_enrollment_unpaid(
    student: StudentProfile,
    enrollment_type: str,
    enrollment_id: int,
    actor,
    reason: str = "",
) -> int:
    """Set all paid PaymentRecords for this enrollment to unpaid.

    Mirrors the immutable before/after audit pattern of record edits: the
    actor and reason are mandatory workflow inputs, and the changed records
    are captured before the bulk update.
    """
    filters = {"student": student, "status": PAYMENT_STATUS_PAID, "deleted_at__isnull": True}
    if enrollment_type == "diploma":
        filters["diploma_enrollment_id"] = enrollment_id
    elif enrollment_type == "course":
        filters["course_enrollment_id"] = enrollment_id
    else:
        raise ValueError(_("Invalid enrollment type: {t}").format(t=enrollment_type))
    records = list(PaymentRecord.objects.filter(**filters).order_by("id"))
    if not records:
        return 0
    before = [
        {
            "id": record.id,
            "amount": str(record.amount),
            "status": record.status,
            "paid_at": record.paid_at.isoformat() if record.paid_at else None,
        }
        for record in records
    ]
    count = PaymentRecord.objects.filter(pk__in=[record.pk for record in records]).update(
        status=PAYMENT_STATUS_UNPAID
    )
    record_audit_event(
        actor=actor,
        action=ACTION_PAYMENT_RECORD_UPDATED,
        target_type="PaymentRecord",
        target_id=str(records[0].id),
        metadata={
            "student_id": student.id,
            "enrollment_type": enrollment_type,
            "enrollment_id": enrollment_id,
            "reason": reason,
            "before": before,
            "after": [
                {**state, "status": PAYMENT_STATUS_UNPAID}
                for state in before
            ],
        },
    )
    return count
