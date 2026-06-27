from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.academics.models import CourseEnrollment, DiplomaEnrollment
from apps.applications.models import Application
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


def normalize_national_id(value: str) -> str:
    return re.sub(r"\s+", "", value).strip()


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
    national_id = normalize_national_id(row.get("national_id", ""))
    email = row.get("email", "").strip().lower()
    phone = normalize_phone(row.get("phone", ""))
    candidates: dict[int, StudentProfile] = {}
    sources: dict[int, str] = {}

    if national_id:
        qs = StudentProfile.objects.filter(national_id=national_id)
        if qs.count() == 1:
            s = qs.first()
            candidates[s.id] = s
            sources[s.id] = "national_id"
        elif qs.count() > 1:
            return None, MATCH_METHOD_AMBIGUOUS, f"Multiple students match national ID {national_id}"

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


def _lookup_application(data: dict, student: StudentProfile) -> Application | None:
    app_id = data.get("application_id", "").strip()
    if not app_id:
        return None
    try:
        return Application.objects.filter(id=int(app_id), student=student).first()
    except (ValueError, TypeError):
        return None


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


def _build_target_snapshot(row) -> tuple[str, dict]:
    if row.matched_diploma_enrollment:
        enroll = row.matched_diploma_enrollment
        return "diploma", {
            "cohort_name": enroll.cohort.name if enroll.cohort else "",
            "diploma_title": enroll.cohort.diploma.title if enroll.cohort and enroll.cohort.diploma else "",
        }
    if row.matched_course_enrollment:
        enroll = row.matched_course_enrollment
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
            row.matched_application = _lookup_application(data, student)
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


@transaction.atomic
def confirm_upload(upload_id: int, confirmed_by) -> PaymentUpload:
    upload = PaymentUpload.objects.get(id=upload_id)
    if upload.status != UPLOAD_STATUS_PREVIEWED:
        raise ValueError(_("Upload is not in previewed state"))
    if upload.matched_rows == 0:
        raise ValueError(_("No rows matched; nothing to import"))

    errors: list[str] = []
    created = updated = 0
    for row in upload.rows.filter(status=ROW_STATUS_MATCHED):
        try:
            existing = PaymentRecord.objects.filter(
                student=row.matched_student,
                external_reference=row.external_reference,
            ).first() if row.external_reference else None
            target_type, target_snapshot = _build_target_snapshot(row)
            if existing:
                existing.amount = row.amount
                existing.paid_at = row.paid_at
                existing.status = PAYMENT_STATUS_PAID
                existing.notes = row.raw_data.get("notes", "")
                existing.imported_by = confirmed_by
                existing.application = row.matched_application
                existing.diploma_enrollment = row.matched_diploma_enrollment
                existing.course_enrollment = row.matched_course_enrollment
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
                updated += 1
            else:
                PaymentRecord.objects.create(
                    student=row.matched_student,
                    upload_row=row,
                    external_reference=row.external_reference or "",
                    amount=row.amount,
                    currency=row.currency,
                    status=PAYMENT_STATUS_PAID,
                    paid_at=row.paid_at,
                    notes=row.raw_data.get("notes", ""),
                    imported_by=confirmed_by,
                    application=row.matched_application,
                    diploma_enrollment=row.matched_diploma_enrollment,
                    course_enrollment=row.matched_course_enrollment,
                    target_type=row.raw_data.get("target_type", target_type),
                    target_ref=row.raw_data.get("target_ref", ""),
                    target_snapshot=target_snapshot,
                )
                row.status = ROW_STATUS_CREATED
                created += 1
            row.save(update_fields=["status"])
        except Exception as e:
            errors.append(f"Row {row.row_number}: {e}")

    if errors:
        raise ValueError(_("Import failed for {count} row(s)").format(count=len(errors)))

    upload.created_rows = created
    upload.updated_rows = updated
    upload.status = UPLOAD_STATUS_CONFIRMED
    upload.completed_at = timezone.now()
    upload.save(
        update_fields=[
            "created_rows", "updated_rows", "failed_rows",
            "status", "completed_at",
        ]
    )
    return upload


def parse_row_data(row: dict[str, str], row_number: int) -> dict[str, Any]:
    amount_raw = row.get("amount", "")
    try:
        amount = Decimal(amount_raw) if amount_raw else None
    except InvalidOperation:
        return {"status": ROW_STATUS_FAILED, "error_message": f"Invalid amount: {amount_raw}"}
    paid_at = parse_date(row.get("payment_date", ""))
    if not paid_at and row.get("payment_date", "").strip():
        return {"status": ROW_STATUS_FAILED, "error_message": f"Invalid date: {row.get('payment_date', '')}"}
    national_id = normalize_national_id(row.get("national_id", ""))
    if not national_id:
        return {"status": ROW_STATUS_FAILED, "error_message": "Empty national ID"}
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
    kwargs: dict = {
        "student": student,
        "amount": Decimal(str(amount)) if amount else Decimal("0"),
        "currency": CURRENCY_EGP,
        "status": PAYMENT_STATUS_PAID,
        "paid_at": timezone.now(),
        "imported_by": actor,
        "notes": f"Manual payment by {actor.email}",
        "target_type": enrollment_type,
    }
    if enrollment_type == "diploma":
        enrollment = DiplomaEnrollment.objects.get(pk=enrollment_id, student=student)
        kwargs["diploma_enrollment"] = enrollment
        kwargs["target_ref"] = str(enrollment.id)
        kwargs["target_snapshot"] = {
            "cohort_name": enrollment.cohort.name if enrollment.cohort else "",
            "diploma_title": enrollment.cohort.diploma.title if enrollment.cohort and enrollment.cohort.diploma else "",
        }
    elif enrollment_type == "course":
        enrollment = CourseEnrollment.objects.get(pk=enrollment_id, student=student)
        kwargs["course_enrollment"] = enrollment
        kwargs["target_ref"] = str(enrollment.id)
        kwargs["target_snapshot"] = {
            "course_title": enrollment.course_version.course.title if enrollment.course_version and enrollment.course_version.course else "",
            "course_version_name": enrollment.course_version.label if enrollment.course_version else "",
        }
    else:
        raise ValueError(_("Invalid enrollment type: {t}").format(t=enrollment_type))
    app = student.applications.order_by("-created_at").first()
    if app:
        kwargs["application"] = app
    return PaymentRecord.objects.create(**kwargs)


def mark_enrollment_unpaid(student: StudentProfile, enrollment_type: str, enrollment_id: int) -> int:
    """Set all paid PaymentRecords for this enrollment to unpaid. Returns count."""

    filters = {"student": student, "status": PAYMENT_STATUS_PAID}
    if enrollment_type == "diploma":
        filters["diploma_enrollment_id"] = enrollment_id
    elif enrollment_type == "course":
        filters["course_enrollment_id"] = enrollment_id
    else:
        raise ValueError(_("Invalid enrollment type: {t}").format(t=enrollment_type))
    return PaymentRecord.objects.filter(**filters).update(status=PAYMENT_STATUS_UNPAID)
