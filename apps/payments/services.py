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
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.academics.models import CourseEnrollment, DiplomaEnrollment
from apps.applications.services import activate_application_if_ready
from apps.audit.constants import (
    ACTION_PAYMENT_RECORD_UPDATED,
    ACTION_PAYMENT_WAIVER_GRANTED,
    ACTION_PAYMENT_WAIVER_REVOKED,
)
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
    MAX_CSV_ROWS,
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
    WAIVER_STATUS_ACTIVE,
    WAIVER_STATUS_REVOKED,
)
from .models import PaymentRecord, PaymentUpload, PaymentUploadRow, PaymentWaiver

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
    for row_number, row in enumerate(reader, start=1):
        if row_number > MAX_CSV_ROWS:
            raise ValueError(
                _("CSV exceeds the maximum of %(count)s rows.") % {"count": MAX_CSV_ROWS}
            )
        cleaned = {k.strip().lower(): (v or "").strip() for k, v in row.items()}
        rows.append(cleaned)
    return rows


def match_student(row: dict[str, str]) -> tuple[StudentProfile | None, str, str]:
    identity_variants = [
        item for item in normalize_national_id_for_matching(row.get("national_id", "")) if item
    ]
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


def _build_student_matcher(rows):
    """Build one set-based identifier index for a bounded CSV batch."""
    identities = set()
    emails = set()
    phones = set()
    for row in rows:
        identities.update(
            item for item in normalize_national_id_for_matching(row.get("national_id", "")) if item
        )
        email = row.get("email", "").strip().lower()
        phone = normalize_phone(row.get("phone", ""))
        if email:
            emails.add(email)
        if phone:
            phones.add(phone)

    candidates = (
        StudentProfile.objects.select_related("user")
        .annotate(normalized_email_db=Lower("user__email"))
        .filter(
            Q(national_id__in=identities)
            | Q(normalized_email_db__in=emails)
            | Q(phone__in=phones)
        )
    )
    by_identity = {}
    by_email = {}
    by_phone = {}
    for student in candidates:
        if student.national_id:
            by_identity.setdefault(student.national_id, []).append(student)
        email = (student.user.email or "").strip().lower()
        if email:
            by_email.setdefault(email, []).append(student)
        if student.phone:
            by_phone.setdefault(normalize_phone(student.phone), []).append(student)

    def resolve(row):
        matches = {}
        methods = {}
        identity_values = [
            item for item in normalize_national_id_for_matching(row.get("national_id", "")) if item
        ]
        email = row.get("email", "").strip().lower()
        phone = normalize_phone(row.get("phone", ""))
        for value in identity_values:
            students = by_identity.get(value, [])
            if len(students) > 1:
                return None, MATCH_METHOD_AMBIGUOUS, f"Multiple students match national ID {value}"
            if students:
                matches[students[0].pk] = students[0]
                methods[students[0].pk] = MATCH_METHOD_NATIONAL_ID
        for value, index, method, label in (
            (email, by_email, MATCH_METHOD_EMAIL, "email"),
            (phone, by_phone, MATCH_METHOD_PHONE, "phone"),
        ):
            if not value:
                continue
            students = index.get(value, [])
            if len(students) > 1:
                return None, MATCH_METHOD_AMBIGUOUS, f"Multiple students match {label} {value}"
            if students:
                matches[students[0].pk] = students[0]
                methods.setdefault(students[0].pk, method)
        if not matches:
            return None, MATCH_METHOD_NONE, "No student found matching any identifier"
        if len(matches) > 1:
            return None, MATCH_METHOD_AMBIGUOUS, "Conflicting identifiers match different students"
        student = next(iter(matches.values()))
        return student, methods[student.pk], ""

    return resolve


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

    parsed_rows = list(upload.rows.filter(status=ROW_STATUS_PARSED))
    resolve_student = _build_student_matcher([row.raw_data for row in parsed_rows])
    for row in parsed_rows:
        data = row.raw_data
        student, method, error = resolve_student(data)
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
    resolve_student = _build_student_matcher([row.raw_data for row in rows])
    errors: list[str] = []
    plans: list[tuple[PaymentUploadRow, StudentProfile, Any, DiplomaEnrollment | None, CourseEnrollment | None, PaymentRecord | None]] = []
    for row in rows:
        try:
            student, method, error = resolve_student(row.raw_data)
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

    # Activation performs a separate domain transition. Defer it until this
    # atomic file import is durably committed so activation failures cannot
    # extend or roll back the payment transaction.
    application_ids = {app.pk for _row, _student, app, *_rest in plans if app}
    actor_id = getattr(confirmed_by, "pk", None)

    def activate_linked_applications():
        from django.contrib.auth import get_user_model
        from apps.applications.models import ProgramApplication

        actor = get_user_model().objects.filter(pk=actor_id).first() if actor_id else None
        applications = ProgramApplication.objects.filter(pk__in=application_ids)
        for application in applications:
            try:
                activate_application_if_ready(application, actor=actor)
            except Exception as exc:
                logger.warning("Activation failed for application %s: %s", application.pk, exc)

    if application_ids:
        transaction.on_commit(activate_linked_applications)
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
    if amount is None or amount < 0:
        return {"status": ROW_STATUS_FAILED, "error_message": str(_("Amount is required and cannot be negative."))}
    paid_at = parse_date(row.get("payment_date", ""))
    if not paid_at:
        return {"status": ROW_STATUS_FAILED, "error_message": str(_("Invalid date: %(date)s") % {"date": row.get("payment_date", "")})}
    national_id = normalize_national_id(row.get("national_id", ""))
    email = row.get("email", "").strip().lower()
    phone = normalize_phone(row.get("phone", ""))
    if not any((national_id, email, phone)):
        return {
            "status": ROW_STATUS_FAILED,
            "error_message": str(_("At least one identifier is required: national ID, email, or phone.")),
        }
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
    row_objects = []
    for i, row in enumerate(rows, start=1):
        parsed = parse_row_data(row, i)
        row_objects.append(PaymentUploadRow(
            upload=upload,
            row_number=i,
            **parsed,
        ))
    PaymentUploadRow.objects.bulk_create(row_objects, batch_size=500)
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


# ---------------------------------------------------------------------------
# Payment waiver (P2-08)
# ---------------------------------------------------------------------------

# Application statuses in which payment readiness is still meaningful. A waiver
# must never be granted for a rejected or already-activated application.
WAIVER_LEGAL_APPLICATION_STATUSES = frozenset({
    "accepted",
    "payment_pending",
    "documents_requested",
    "documents_submitted",
    "survey_requested",
    "survey_submitted",
    "ready_for_activation",
})


def _build_waiver_target_snapshot(application) -> dict:
    snapshot = {
        "program_type": application.program_type or "",
        "status": application.status or "",
    }
    if application.cohort:
        snapshot["cohort_name"] = application.cohort.name or ""
        if application.cohort.diploma:
            snapshot["diploma_title"] = application.cohort.diploma.title or ""
    if application.course:
        snapshot["course_title"] = application.course.title or ""
    return snapshot


def _waiver_requirement_state(application) -> dict:
    return {
        "paid": bool(
            PaymentRecord.objects.filter(
                application=application,
                status=PAYMENT_STATUS_PAID,
                deleted_at__isnull=True,
            ).exists()
        ),
        "waiver_active": PaymentWaiver.objects.filter(
            application=application,
            status=WAIVER_STATUS_ACTIVE,
        ).exists(),
        "application_status": application.status or "",
    }


def has_active_payment_waiver(application) -> bool:
    """Whether an active Admin waiver satisfies payment readiness."""
    return PaymentWaiver.objects.filter(
        application=application,
        status=WAIVER_STATUS_ACTIVE,
    ).exists()


def get_active_payment_waiver(application) -> PaymentWaiver | None:
    return PaymentWaiver.objects.filter(
        application=application,
        status=WAIVER_STATUS_ACTIVE,
    ).first()


def _payment_requirement_satisfied(application) -> bool:
    """Payment readiness: a paid record OR an active Admin waiver."""
    return has_active_payment_waiver(application) or bool(
        PaymentRecord.objects.filter(
            application=application,
            status=PAYMENT_STATUS_PAID,
            deleted_at__isnull=True,
        ).exists()
    )


@transaction.atomic
def grant_payment_waiver(application, actor, *, reason: str) -> PaymentWaiver:
    """Grant an Admin/Super Admin payment waiver for one exact application.

    The waiver is an audited override: it never mutates a PaymentRecord and
    records requirement state before/after. Exactly one active waiver may
    exist per application (also enforced by a DB partial unique constraint).
    Activation is attempted only if the waiver is the missing readiness gate.
    """
    from apps.applications.models import ProgramApplication

    if not reason or not reason.strip():
        raise ValueError(_("Waiver reason is required."))

    locked = ProgramApplication.objects.select_for_update().get(pk=application.pk)
    if locked.status not in WAIVER_LEGAL_APPLICATION_STATUSES:
        raise ValueError(_("Cannot grant a payment waiver for the current application status."))

    before = _waiver_requirement_state(locked)
    if PaymentWaiver.objects.filter(
        application=locked,
        status=WAIVER_STATUS_ACTIVE,
    ).exists():
        raise ValueError(_("An active payment waiver already exists for this application."))

    after = {**before, "waiver_active": True}
    waiver = PaymentWaiver.objects.create(
        student=locked.student,
        application=locked,
        target_snapshot=_build_waiver_target_snapshot(locked),
        reason=reason.strip(),
        requirement_before=before,
        requirement_after=after,
        granted_by=actor,
        granted_at=timezone.now(),
    )

    record_audit_event(
        actor=actor,
        action=ACTION_PAYMENT_WAIVER_GRANTED,
        target_type="PaymentWaiver",
        target_id=str(waiver.pk),
        metadata={
            "application_id": str(locked.pk),
            "student_id": locked.student_id,
            "reason": waiver.reason,
            "requirement_before": before,
            "requirement_after": after,
        },
    )

    # Attempt activation only if payment readiness is now satisfied and the
    # waiver was the blocking gate; other gates (intake, documents) remain.
    try:
        activate_application_if_ready(locked, actor=actor)
    except Exception:
        logger.exception("Activation attempt after waiver grant failed for application %s", locked.pk)

    return waiver


@transaction.atomic
def revoke_payment_waiver(waiver, actor, *, reason: str = "") -> PaymentWaiver:
    """Revoke an active payment waiver (audited transition).

    The owning application row is locked alongside the waiver so a concurrent
    activation (which locks the application) cannot race the readiness flip.
    """
    from apps.applications.models import ProgramApplication

    locked = PaymentWaiver.objects.select_for_update().get(pk=waiver.pk)
    if locked.application_id:
        ProgramApplication.objects.select_for_update().get(pk=locked.application_id)
    if locked.status != WAIVER_STATUS_ACTIVE:
        raise ValueError(_("This payment waiver is not active."))
    locked.status = WAIVER_STATUS_REVOKED
    locked.revoked_by = actor
    locked.revoked_at = timezone.now()
    locked.revoke_reason = reason.strip()
    locked.save(
        update_fields=[
            "status", "revoked_by", "revoked_at", "revoke_reason", "updated_at",
        ]
    )
    record_audit_event(
        actor=actor,
        action=ACTION_PAYMENT_WAIVER_REVOKED,
        target_type="PaymentWaiver",
        target_id=str(locked.pk),
        metadata={
            "application_id": str(locked.application_id) if locked.application_id else "",
            "student_id": locked.student_id,
            "reason": locked.revoke_reason,
        },
    )
    return locked
