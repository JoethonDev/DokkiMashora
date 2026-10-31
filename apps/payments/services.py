from __future__ import annotations

import csv
import hashlib
import io
import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.academics.models import CourseEnrollment, DiplomaEnrollment
from apps.applications import services as application_services
from apps.applications.constants import PROGRAM_TYPE_DIPLOMA
from apps.applications.models import ProgramApplication
from apps.audit.constants import (
    ACTION_PAYMENT_RECORD_DELETED,
    ACTION_PAYMENT_RECORD_UPDATED,
    ACTION_PAYMENT_WAIVER_GRANTED,
    ACTION_PAYMENT_WAIVER_REVOKED,
    SECTION_PAYMENTS,
)
from apps.audit.services import record_audit_event
from apps.core.dates import parse_date
from apps.core.permissions import validate_score
from apps.core.phone import normalize_phone
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
        enrollment = DiplomaEnrollment.objects.select_related("cohort__diploma").filter(
            id=int(enroll_id), student=student,
        ).first()
    except (ValueError, TypeError):
        raise ValueError(_("Invalid diploma enrollment ID.")) from None
    if enrollment is None:
        raise ValueError(_("Diploma enrollment ID does not match this student."))
    return enrollment


def _lookup_course_enrollment(data: dict, student: StudentProfile) -> CourseEnrollment | None:
    enroll_id = data.get("course_enrollment_id", "").strip()
    if not enroll_id:
        return None
    try:
        enrollment = CourseEnrollment.objects.select_related("course_version__course").filter(
            id=int(enroll_id), student=student,
        ).first()
    except (ValueError, TypeError):
        raise ValueError(_("Invalid course enrollment ID.")) from None
    if enrollment is None:
        raise ValueError(_("Course enrollment ID does not match this student."))
    return enrollment


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


def _payment_audit_target_label(student, snapshot: dict | None) -> str:
    """Human target label from the already-persisted snapshot (no extra queries)."""
    snapshot = snapshot or {}
    parts = [str(getattr(student, "full_name", "") or student.pk)]
    program = (
        snapshot.get("diploma_title")
        or snapshot.get("course_title")
        or snapshot.get("program_title")
    )
    if program:
        parts.append(str(program))
    detail = snapshot.get("cohort_name") or snapshot.get("course_version_name")
    if detail:
        parts.append(str(detail))
    return " · ".join(part for part in parts if part)


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
                all_parsed = False
                row.save(update_fields=["status", "error_message"])
                failed += 1
                continue
            try:
                diploma_enrollment = _lookup_diploma_enrollment(data, student)
                course_enrollment = _lookup_course_enrollment(data, student)
            except ValueError as exc:
                row.status = ROW_STATUS_FAILED
                row.error_message = str(exc)
                errors.append(f"Row {row.row_number}: {exc}")
                all_parsed = False
                row.save(update_fields=["status", "error_message"])
                failed += 1
                continue
            row.matched_application = app
            row.matched_diploma_enrollment = diploma_enrollment
            row.matched_course_enrollment = course_enrollment
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
    """Return the PaymentRecord this row should update, or None to create a new one.

    Duplicate proof is business semantics, never the amount/date heuristic:
    - a row with an external reference updates the matching reference for the
      same student (a true re-imported duplicate row);
    - otherwise an already-paid, non-deleted PaymentRecord for the same
      application, diploma enrollment, or course enrollment is the same payment
      business effect already applied and is updated instead.
    Reference-less rows with no such target record create a new PaymentRecord
    rather than collapsing distinct legitimate payments.
    """
    if row.external_reference:
        return PaymentRecord.objects.filter(
            student=row.matched_student,
            external_reference=row.external_reference,
            deleted_at__isnull=True,
        ).first()
    if row.matched_application_id:
        return PaymentRecord.objects.filter(
            student=row.matched_student,
            application_id=row.matched_application_id,
            status=PAYMENT_STATUS_PAID,
            deleted_at__isnull=True,
        ).first()
    if row.matched_diploma_enrollment_id:
        return PaymentRecord.objects.filter(
            student=row.matched_student,
            diploma_enrollment_id=row.matched_diploma_enrollment_id,
            status=PAYMENT_STATUS_PAID,
            deleted_at__isnull=True,
        ).first()
    if row.matched_course_enrollment_id:
        return PaymentRecord.objects.filter(
            student=row.matched_student,
            course_enrollment_id=row.matched_course_enrollment_id,
            status=PAYMENT_STATUS_PAID,
            deleted_at__isnull=True,
        ).first()
    return None


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
        actor = get_user_model().objects.filter(pk=actor_id).first() if actor_id else None
        applications = ProgramApplication.objects.filter(pk__in=application_ids)
        for application in applications:
            try:
                application_services.activate_application_if_ready(application, actor=actor)
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
        record = reconcile_payment_record(
            actor=confirmed_by,
            record_id=existing.pk if existing else None,
            student=student,
            application=app,
            amount=row.amount,
            status=PAYMENT_STATUS_PAID,
            paid_at=row.paid_at,
            external_reference=row.external_reference or "",
            notes=row.raw_data.get("notes", ""),
            diploma_enrollment=diploma_enrollment,
            course_enrollment=course_enrollment,
            target_type=row.raw_data.get("target_type", target_type),
            target_ref=row.raw_data.get("target_ref", ""),
            target_snapshot=target_snapshot,
            source="import",
        )
        if not existing:
            record.upload_row = row
            record.save(update_fields=["upload_row", "updated_at"])
        row.status = ROW_STATUS_UPDATED if existing else ROW_STATUS_CREATED
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
def reconcile_payment_record(
    *,
    actor,
    status: str,
    amount: Decimal | int,
    record_id: int | None = None,
    student: StudentProfile | None = None,
    application=None,
    paid_at=None,
    due_at=None,
    external_reference: str = "",
    notes: str = "",
    diploma_enrollment=None,
    course_enrollment=None,
    target_type: str = "",
    target_ref: str = "",
    target_snapshot: dict | None = None,
    reason: str = "",
    source: str = "manual",
    attempt_activation: bool = False,
    soft_delete: bool = False,
) -> PaymentRecord:
    """Own every manual/import payment status write and its reconciliation.

    Every application-linked paid write enforces the admissions gates, including
    imports. Non-application financial records remain independent of admissions;
    activation is always attempted only through the normal readiness service.
    A paid-to-unpaid correction cannot leave an active application without a
    proven payment/waiver dependency or a rollback of the exact activation.
    """
    valid_statuses = {value for value, _label in PaymentRecord._meta.get_field("status").choices}
    if status not in valid_statuses:
        raise ValueError(_("Invalid payment status."))

    record = None
    if record_id is not None:
        record = PaymentRecord.objects.select_for_update(of=("self",)).select_related(
            "application", "student", "diploma_enrollment", "course_enrollment",
        ).get(pk=record_id)
        if application is None:
            application = record.application
        elif record.application_id and record.application_id != application.pk:
            raise ValueError(_("The payment record does not belong to this application."))
        if student is None:
            student = record.student
        elif record.student_id != student.pk:
            raise ValueError(_("The payment record does not belong to this student."))

    locked_application = None
    if application is not None:
        locked_application = ProgramApplication.objects.select_for_update().get(pk=application.pk)
        if student is None:
            student = locked_application.student
        elif locked_application.student_id != student.pk:
            raise ValueError(_("The payment application does not belong to this student."))

    if student is None:
        raise ValueError(_("A student is required for a payment record."))

    effective_paid_after = status == PAYMENT_STATUS_PAID and not soft_delete
    was_paid = bool(record and record.status == PAYMENT_STATUS_PAID and record.deleted_at is None)

    if (
        locked_application is not None
        and status == PAYMENT_STATUS_PAID
        and not was_paid
    ):
        if locked_application.status not in application_services.LEGAL_PAYMENT_STATUSES:
            raise ValueError(_("Cannot record payment for the current application status."))
        if (
            locked_application.program_type == PROGRAM_TYPE_DIPLOMA
            and not application_services.has_application_documents_submitted(locked_application)
        ):
            raise ValueError(_("Application documents must be submitted before recording payment."))
        if not application_services.has_required_intake_response(locked_application):
            raise ValueError(_("Required intake form must be submitted before recording payment."))
        if application_services.has_active_application_waiver(locked_application):
            raise ValueError(_("An active payment waiver already exists for this application."))

    if effective_paid_after:
        duplicate_qs = PaymentRecord.objects.filter(
            student=student,
            status=PAYMENT_STATUS_PAID,
            deleted_at__isnull=True,
        )
        if locked_application is not None:
            duplicate_qs = duplicate_qs.filter(application=locked_application)
        elif diploma_enrollment is not None:
            duplicate_qs = duplicate_qs.filter(diploma_enrollment=diploma_enrollment)
        elif course_enrollment is not None:
            duplicate_qs = duplicate_qs.filter(course_enrollment=course_enrollment)
        if record is not None:
            duplicate_qs = duplicate_qs.exclude(pk=record.pk)
        if duplicate_qs.exists():
            raise ValueError(_("Payment already recorded for this target."))

    if paid_at is None and effective_paid_after:
        paid_at = timezone.now()
    amount_dec = validate_score(amount or 0)
    before = None
    if record is not None:
        before = {
            "status": record.status,
            "deleted_at": record.deleted_at.isoformat() if record.deleted_at else None,
            "amount": str(record.amount),
            "paid_at": record.paid_at.isoformat() if record.paid_at else None,
        }
        record.amount = amount_dec
        record.status = status
        record.paid_at = paid_at
        record.due_at = due_at
        record.external_reference = external_reference
        record.notes = notes
        record.imported_by = actor
        if application is not None:
            record.application = locked_application
        if diploma_enrollment is not None:
            record.diploma_enrollment = diploma_enrollment
        if course_enrollment is not None:
            record.course_enrollment = course_enrollment
        if target_type:
            record.target_type = target_type
        if target_ref:
            record.target_ref = target_ref
        if target_snapshot is not None:
            record.target_snapshot = target_snapshot
        if soft_delete:
            record.deleted_at = timezone.now()
            record.deleted_by = actor
        record.save()
    else:
        record = PaymentRecord.objects.create(
            student=student,
            application=locked_application,
            amount=amount_dec,
            currency=CURRENCY_EGP,
            status=status,
            paid_at=paid_at,
            due_at=due_at,
            external_reference=external_reference,
            notes=notes,
            imported_by=actor,
            diploma_enrollment=diploma_enrollment,
            course_enrollment=course_enrollment,
            target_type=target_type,
            target_ref=target_ref,
            target_snapshot=target_snapshot or {},
        )

    if locked_application is not None and was_paid and not effective_paid_after:
        other_paid = PaymentRecord.objects.filter(
            application=locked_application,
            status=PAYMENT_STATUS_PAID,
            deleted_at__isnull=True,
        ).exclude(pk=record.pk).exists()
        has_waiver = application_services.has_active_application_waiver(locked_application)
        if locked_application.status == "activated" and not other_paid and not has_waiver:
            rollback = application_services.rollback_payment_activation(
                locked_application.pk,
                record.pk,
                actor=actor,
                reason=reason,
            )
            if not rollback["rolled_back"]:
                raise ValueError(_(
                    "This correction is not proven to own the active access. Use a governed correction workflow."
                ))
    elif locked_application is None and was_paid and not effective_paid_after:
        enrollment = diploma_enrollment or course_enrollment or getattr(record, "diploma_enrollment", None) or getattr(record, "course_enrollment", None)
        if enrollment is not None and enrollment.status == "active":
            raise ValueError(_(
                "This correction would remove payment from active access without a proven activation dependency."
            ))

    record_audit_event(
        actor=actor,
        action=ACTION_PAYMENT_RECORD_DELETED if soft_delete else ACTION_PAYMENT_RECORD_UPDATED,
        target_type="PaymentRecord",
        target_id=str(record.pk),
        section=SECTION_PAYMENTS,
        target_label=_payment_audit_target_label(student, record.target_snapshot),
        subject_type="student",
        subject_id=str(student.id),
        subject_label=student.full_name,
        metadata={
            "source": source,
            "reason": reason,
            "target_type": record.target_type or "",
            "target_ref": record.target_ref or "",
            "status_before": before["status"] if before else "",
            "status_after": record.status,
            "amount_before": before["amount"] if before else "",
            "amount_after": str(record.amount),
            "currency": record.currency,
            "before": before,
            "after": {
                "status": record.status,
                "deleted_at": record.deleted_at.isoformat() if record.deleted_at else None,
                "amount": str(record.amount),
                "paid_at": record.paid_at.isoformat() if record.paid_at else None,
            },
        },
    )

    if locked_application is not None and attempt_activation and effective_paid_after:
        application_services.activate_application_if_ready(locked_application, actor=actor)
    return record


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
    if enrollment_type == "diploma":
        enrollment = DiplomaEnrollment.objects.select_for_update().get(pk=enrollment_id, student=student)
        target_snapshot = {
            "cohort_name": enrollment.cohort.name if enrollment.cohort else "",
            "diploma_title": enrollment.cohort.diploma.title if enrollment.cohort and enrollment.cohort.diploma else "",
        }
    elif enrollment_type == "course":
        enrollment = CourseEnrollment.objects.select_for_update().get(pk=enrollment_id, student=student)
        target_snapshot = {
            "course_title": enrollment.course_version.course.title if enrollment.course_version and enrollment.course_version.course else "",
            "course_version_name": enrollment.course_version.label if enrollment.course_version else "",
        }
    else:
        raise ValueError(_("Invalid enrollment type: {t}").format(t=enrollment_type))
    return reconcile_payment_record(
        actor=actor,
        student=student,
        amount=amount,
        status=PAYMENT_STATUS_PAID,
        notes=f"Manual payment by {actor.email}",
        diploma_enrollment=enrollment if enrollment_type == "diploma" else None,
        course_enrollment=enrollment if enrollment_type == "course" else None,
        target_type=enrollment_type,
        target_ref=str(enrollment.id),
        target_snapshot=target_snapshot,
        source="enrollment",
    )


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
    records = list(PaymentRecord.objects.select_for_update().filter(**filters).order_by("id"))
    if not records:
        return 0
    for record in records:
        reconcile_payment_record(
            actor=actor,
            record_id=record.pk,
            student=student,
            amount=record.amount,
            status=PAYMENT_STATUS_UNPAID,
            paid_at=record.paid_at,
            due_at=record.due_at,
            external_reference=record.external_reference,
            notes=record.notes,
            diploma_enrollment=record.diploma_enrollment,
            course_enrollment=record.course_enrollment,
            reason=reason,
            source="enrollment",
        )
    return len(records)


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


@transaction.atomic
def grant_payment_waiver(application, actor, *, reason: str) -> PaymentWaiver:
    """Grant an Admin/Super Admin payment waiver for one exact application.

    The waiver is an audited override: it never mutates a PaymentRecord and
    records requirement state before/after. Exactly one active waiver may
    exist per application (also enforced by a DB partial unique constraint).
    Activation is attempted only if the waiver is the missing readiness gate.
    """
    if not reason or not reason.strip():
        raise ValueError(_("Waiver reason is required."))

    locked = ProgramApplication.objects.select_for_update().get(pk=application.pk)
    if locked.status not in WAIVER_LEGAL_APPLICATION_STATUSES:
        raise ValueError(_("Cannot grant a payment waiver for the current application status."))
    if application_services.has_application_paid_record(locked):
        raise ValueError(_("A payment waiver cannot be granted after payment has been recorded."))
    if (
        locked.program_type == PROGRAM_TYPE_DIPLOMA
        and not application_services.has_application_documents_submitted(locked)
    ):
        raise ValueError(_("Application documents must be submitted before granting a payment waiver."))
    if not application_services.has_required_intake_response(locked):
        raise ValueError(_("Required intake form must be submitted before granting a payment waiver."))

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
        section=SECTION_PAYMENTS,
        target_label=_payment_audit_target_label(locked.student, waiver.target_snapshot),
        subject_type="student",
        subject_id=str(locked.student_id),
        subject_label=locked.student.full_name,
        metadata={
            "application_id": str(locked.pk),
            "student_id": locked.student_id,
            "reason": waiver.reason,
            "status_before": "",
            "status_after": waiver.status,
            "application_status_before": before.get("application_status", ""),
            "application_status_after": after.get("application_status", ""),
            "requirement_before": before,
            "requirement_after": after,
        },
    )

    # Attempt activation only if payment readiness is now satisfied and the
    # waiver was the blocking gate; other gates (intake, documents) remain.
    try:
        application_services.activate_application_if_ready(locked, actor=actor)
    except Exception:
        logger.exception("Activation attempt after waiver grant failed for application %s", locked.pk)

    return waiver


@transaction.atomic
def revoke_payment_waiver(waiver, actor, *, reason: str = "") -> PaymentWaiver:
    """Revoke an active payment waiver (audited transition).

    The owning application row is locked alongside the waiver so a concurrent
    activation (which locks the application) cannot race the readiness flip.
    """
    locked = PaymentWaiver.objects.select_for_update().get(pk=waiver.pk)
    if locked.application_id:
        ProgramApplication.objects.select_for_update().get(pk=locked.application_id)
    if locked.status != WAIVER_STATUS_ACTIVE:
        raise ValueError(_("This payment waiver is not active."))
    application_id = locked.application_id
    locked.status = WAIVER_STATUS_REVOKED
    locked.revoked_by = actor
    locked.revoked_at = timezone.now()
    locked.revoke_reason = reason.strip()
    locked.save(
        update_fields=[
            "status", "revoked_by", "revoked_at", "revoke_reason", "updated_at",
        ]
    )
    rollback = {"rolled_back": False, "application_status": ""}
    if application_id:
        rollback = application_services.rollback_waiver_activation(
            application_id,
            locked.pk,
            actor=actor,
            reason=reason.strip(),
        )
    record_audit_event(
        actor=actor,
        action=ACTION_PAYMENT_WAIVER_REVOKED,
        target_type="PaymentWaiver",
        target_id=str(locked.pk),
        section=SECTION_PAYMENTS,
        target_label=_payment_audit_target_label(locked.student, locked.target_snapshot),
        subject_type="student",
        subject_id=str(locked.student_id),
        subject_label=locked.student.full_name,
        metadata={
            "application_id": str(locked.application_id) if locked.application_id else "",
            "student_id": locked.student_id,
            "reason": locked.revoke_reason,
            "status_before": WAIVER_STATUS_ACTIVE,
            "status_after": locked.status,
            "application_status_before": rollback.get("application_status_before", ""),
            "application_status_after": rollback.get("application_status_after", ""),
            "activation_rolled_back": rollback.get("rolled_back", False),
            "enrollment_reset": rollback.get("enrollment_reset", False),
        },
    )
    return locked
