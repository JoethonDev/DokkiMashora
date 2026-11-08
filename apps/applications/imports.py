"""Historical application bulk-import services.

This is the only path allowed to create an activated application and
enrollment without the live admissions gates. It is deliberately separate
from ``activate_application_if_ready``: no email, payment, document, intake,
entrance-exam, or certificate side effect is produced here.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from apps.academics.constants import (
    ENROLLMENT_STATUS_ACTIVE,
    ENROLLMENT_STATUS_COMPLETED,
    ENROLLMENT_STATUS_EXPIRED,
    ENROLLMENT_STATUS_RESTRICTED,
)
from apps.academics.models import (
    Cohort,
    Course,
    CourseEnrollment,
    CourseVersion,
    DiplomaEnrollment,
    Semester,
)
from apps.accounts.constants import MATCH_METHOD_AMBIGUOUS, MATCH_METHOD_NONE
from apps.accounts.services import lock_identity_writes
from apps.attendance.services import get_or_create_attendance_codes_for_enrollments
from apps.audit.constants import (
    ACTION_APPLICATION_IMPORT_CONFIRMED,
    ACTION_APPLICATION_IMPORT_PREVIEWED,
    SECTION_ADMISSIONS,
)
from apps.audit.services import record_audit_event
from apps.core.dates import parse_date
from apps.students.matching import build_student_matcher
from apps.students.models import StudentProfile

from .constants import (
    APPLICATION_IMPORT_IDENTITY_HEADERS,
    APPLICATION_IMPORT_MAX_FILE_SIZE_BYTES,
    APPLICATION_IMPORT_MAX_ROWS,
    APPLICATION_IMPORT_REQUIRED_HEADERS,
    APPLICATION_IMPORT_ROW_AMBIGUOUS,
    APPLICATION_IMPORT_ROW_CREATED,
    APPLICATION_IMPORT_ROW_FAILED,
    APPLICATION_IMPORT_ROW_MATCHED,
    APPLICATION_IMPORT_STATUS_CONFIRMED,
    APPLICATION_IMPORT_STATUS_FAILED,
    APPLICATION_IMPORT_STATUS_PENDING,
    APPLICATION_IMPORT_STATUS_PREVIEWED,
    DECISION_ACTION_ACTIVATE,
    DECISION_ACTION_REJECT,
    DELIVERY_MODE_CHOICES,
    DELIVERY_MODE_ONLINE,
    PROGRAM_APP_STATUS_ACTIVATED,
    PROGRAM_APP_STATUS_REJECTED,
    PROGRAM_TYPE_COURSE,
    PROGRAM_TYPE_DIPLOMA,
)
from .import_errors import make_import_error, render_import_errors
from .models import (
    ApplicationImport,
    ApplicationImportRow,
    ProgramApplication,
    ProgramApplicationDecision,
)


@dataclass
class _Indexes:
    cohort_by_id: dict[int, Cohort]
    cohort_by_key: dict[tuple[str, str], Cohort]
    course_by_id: dict[int, Course]
    courses_by_title: dict[str, list[Course]]
    version_by_id: dict[int, CourseVersion]
    versions_by_course: dict[int, list[CourseVersion]]
    semesters_by_diploma: dict[int, list[Semester]]
    existing_apps: dict[tuple[int, str, int], tuple[int, str]]
    existing_diploma_enrollments: set[tuple[int, int]]
    existing_course_enrollments: set[tuple[int, int]]


@dataclass
class _RowPlan:
    student: StudentProfile
    program_type: str
    cohort: Cohort | None
    course: Course | None
    course_version: CourseVersion | None
    application_status: str
    application_date: datetime
    decision_date: datetime
    activated_at: datetime | None
    expires_at: datetime | None
    completed_at: datetime | None
    enrollment_status: str
    entry_semester: Semester | None
    delivery_mode: str
    rejection_reason: str
    admin_notes: str


def validate_csv_content(content: bytes) -> list[dict[str, str]]:
    """Validate and parse a historical application CSV."""
    if len(content) > APPLICATION_IMPORT_MAX_FILE_SIZE_BYTES:
        raise ValueError(
            _("File size exceeds the maximum of %(size)d MB")
            % {"size": APPLICATION_IMPORT_MAX_FILE_SIZE_BYTES // (1024 * 1024)}
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

    headers = [header.strip().lower() for header in source_headers]
    if len(headers) != len(set(headers)):
        raise ValueError(_("CSV file contains duplicate column names."))
    reader.fieldnames = headers

    missing = [header for header in APPLICATION_IMPORT_REQUIRED_HEADERS if header not in headers]
    if missing:
        raise ValueError(_("Missing required columns: %(columns)s") % {"columns": ", ".join(missing)})
    if not any(header in headers for header in APPLICATION_IMPORT_IDENTITY_HEADERS):
        raise ValueError(
            _("At least one identity column is required: %(columns)s")
            % {"columns": ", ".join(APPLICATION_IMPORT_IDENTITY_HEADERS)}
        )

    rows: list[dict[str, str]] = []
    row_number = 0
    try:
        for row_number, row in enumerate(reader, start=1):
            if row_number > APPLICATION_IMPORT_MAX_ROWS:
                raise ValueError(
                    _("CSV exceeds the maximum of %(count)d rows.") % {"count": APPLICATION_IMPORT_MAX_ROWS}
                )
            extra_values = row.pop(None, [])
            if any((value or "").strip() for value in extra_values):
                raise ValueError(_("Row %(row)d contains more values than the header.") % {"row": row_number})
            rows.append({key: (value or "").strip() for key, value in row.items()})
    except csv.Error:
        raise ValueError(_("CSV file is malformed near row %(row)d.") % {"row": max(row_number, 1)}) from None

    if not rows:
        raise ValueError(_("CSV file contains no data rows."))
    return rows


@transaction.atomic
def create_application_import(*, original_filename: str, content: bytes, uploaded_by) -> ApplicationImport:
    """Create a pending batch and its parsed rows. No domain writes."""
    rows = validate_csv_content(content)
    upload = ApplicationImport.objects.create(
        uploaded_by=uploaded_by,
        original_filename=original_filename,
        file_hash=hashlib.sha256(content).hexdigest(),
        status=APPLICATION_IMPORT_STATUS_PENDING,
        started_at=timezone.now(),
        total_rows=len(rows),
    )
    ApplicationImportRow.objects.bulk_create(
        [
            ApplicationImportRow(
                upload=upload,
                row_number=index,
                raw_data=row,
            )
            for index, row in enumerate(rows, start=1)
        ],
        batch_size=500,
    )
    return upload


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _choice(value: Any) -> str:
    return _clean(value).lower()


def _required_date(raw: dict[str, str], key: str) -> datetime | None:
    return parse_date(raw.get(key, ""))


def _optional_date(raw: dict[str, str], key: str, *, end_of_day: bool = False) -> datetime | None:
    parsed = parse_date(raw.get(key, ""))
    if parsed is None:
        return None
    if end_of_day:
        return parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed


def _resolve_cohort(raw: dict[str, str], indexes: _Indexes, errors: list[dict]) -> Cohort | None:
    cohort_id_raw = _clean(raw.get("cohort_id"))
    diploma_title = _clean(raw.get("diploma_title"))
    cohort_name = _clean(raw.get("cohort_name"))

    if cohort_id_raw:
        try:
            cohort_id = int(cohort_id_raw)
        except (TypeError, ValueError):
            errors.append(make_import_error(gettext_noop("cohort_id must be a number.")))
            return None
        cohort = indexes.cohort_by_id.get(cohort_id)
        if cohort is None:
            errors.append(
                make_import_error(
                    gettext_noop("Cohort %(cohort_id)s does not exist."),
                    cohort_id=cohort_id_raw,
                )
            )
            return None
        if diploma_title and diploma_title.casefold() != cohort.diploma.title.casefold():
            errors.append(
                make_import_error(gettext_noop("cohort_id does not belong to the supplied diploma_title."))
            )
        if cohort_name and cohort_name.casefold() != cohort.name.casefold():
            errors.append(
                make_import_error(gettext_noop("cohort_id does not match the supplied cohort_name."))
            )
        return cohort

    if not diploma_title or not cohort_name:
        errors.append(
            make_import_error(
                gettext_noop("diploma_title and cohort_name are required for a diploma application.")
            )
        )
        return None
    cohort = indexes.cohort_by_key.get((diploma_title.casefold(), cohort_name.casefold()))
    if cohort is None:
        errors.append(
            make_import_error(
                gettext_noop("No cohort found for diploma '%(diploma)s' and cohort '%(cohort)s'."),
                diploma=diploma_title,
                cohort=cohort_name,
            )
        )
        return None
    return cohort


def _resolve_course(
    raw: dict[str, str], indexes: _Indexes, errors: list[dict]
) -> tuple[Course | None, CourseVersion | None]:
    course_id_raw = _clean(raw.get("course_id"))
    course_title = _clean(raw.get("course_title"))
    course: Course | None = None

    if course_id_raw:
        try:
            course_id = int(course_id_raw)
        except (TypeError, ValueError):
            errors.append(make_import_error(gettext_noop("course_id must be a number.")))
            return None, None
        course = indexes.course_by_id.get(course_id)
        if course is None:
            errors.append(
                make_import_error(
                    gettext_noop("Course %(course_id)s does not exist."),
                    course_id=course_id_raw,
                )
            )
            return None, None
        if course_title and course_title.casefold() != course.title.casefold():
            errors.append(
                make_import_error(gettext_noop("course_id does not match the supplied course_title."))
            )
            return None, None
    else:
        if not course_title:
            errors.append(
                make_import_error(gettext_noop("course_title is required for a course application."))
            )
            return None, None
        matches = indexes.courses_by_title.get(course_title.casefold(), [])
        if not matches:
            errors.append(
                make_import_error(
                    gettext_noop("No course found for title '%(title)s'."),
                    title=course_title,
                )
            )
            return None, None
        if len(matches) > 1:
            errors.append(
                make_import_error(
                    gettext_noop("Multiple courses match '%(title)s'; provide course_id."),
                    title=course_title,
                )
            )
            return None, None
        course = matches[0]

    versions = indexes.versions_by_course.get(course.id, [])
    if not versions:
        errors.append(
            make_import_error(
                gettext_noop("Course '%(title)s' has no versions."),
                title=course.title,
            )
        )
        return course, None

    version_id_raw = _clean(raw.get("course_version_id"))
    version_label = _clean(raw.get("course_version"))

    if version_id_raw:
        try:
            version_id = int(version_id_raw)
        except (TypeError, ValueError):
            errors.append(make_import_error(gettext_noop("course_version_id must be a number.")))
            return course, None
        version = indexes.version_by_id.get(version_id)
        if version is None or version.course_id != course.id:
            errors.append(
                make_import_error(
                    gettext_noop("course_version_id %(version_id)s does not belong to the selected course."),
                    version_id=version_id_raw,
                )
            )
            return course, None
        if version_label and version_label.casefold() != version.label.casefold():
            errors.append(
                make_import_error(
                    gettext_noop("course_version_id does not match the supplied course_version.")
                )
            )
            return course, None
        return course, version

    if version_label:
        matches = [version for version in versions if version.label.casefold() == version_label.casefold()]
        if not matches:
            errors.append(
                make_import_error(
                    gettext_noop("Course version '%(label)s' was not found for '%(course)s'."),
                    label=version_label,
                    course=course.title,
                )
            )
            return course, None
        if len(matches) > 1:
            errors.append(
                make_import_error(
                    gettext_noop("Multiple course versions match '%(label)s'; provide course_version_id."),
                    label=version_label,
                )
            )
            return course, None
        return course, matches[0]

    if len(versions) > 1:
        errors.append(
            make_import_error(
                gettext_noop(
                    "course_version or course_version_id is required because this course has multiple versions."
                )
            )
        )
        return course, None
    return course, versions[0]


def _resolve_entry_semester(
    raw: dict[str, str],
    diploma,
    indexes: _Indexes,
    errors: list[dict],
) -> Semester | None:
    value = _clean(raw.get("entry_semester"))
    semesters = indexes.semesters_by_diploma.get(diploma.id, [])
    if not value:
        return semesters[0] if semesters else None
    if not semesters:
        errors.append(make_import_error(gettext_noop("The selected diploma has no semesters.")))
        return None
    if value.isdigit():
        order = int(value)
        matches = [semester for semester in semesters if semester.order == order]
    else:
        matches = [semester for semester in semesters if semester.title.casefold() == value.casefold()]
    if not matches:
        errors.append(
            make_import_error(
                gettext_noop("Semester '%(semester)s' was not found in '%(diploma)s'."),
                semester=value,
                diploma=diploma.title,
            )
        )
        return None
    if len(matches) > 1:
        errors.append(
            make_import_error(
                gettext_noop("Multiple semesters match '%(semester)s'; use the semester order."),
                semester=value,
            )
        )
        return None
    return matches[0]


def _target_key(student_id: int, program_type: str, target_id: int) -> tuple[int, str, int]:
    return student_id, program_type, target_id


def _parse_plan(
    raw: dict[str, str], student: StudentProfile, indexes: _Indexes
) -> tuple[_RowPlan | None, list[dict]]:
    errors: list[dict] = []
    now = timezone.now()

    program_type = _choice(raw.get("program_type"))
    if program_type not in (PROGRAM_TYPE_DIPLOMA, PROGRAM_TYPE_COURSE):
        return None, [make_import_error(gettext_noop("program_type must be 'diploma' or 'course'."))]

    application_status = _choice(raw.get("application_status"))
    if application_status not in (PROGRAM_APP_STATUS_ACTIVATED, PROGRAM_APP_STATUS_REJECTED):
        return None, [
            make_import_error(gettext_noop("application_status must be 'activated' or 'rejected'."))
        ]

    application_date = _required_date(raw, "application_date")
    if application_date is None:
        return None, [
            make_import_error(gettext_noop("application_date is required and must be a valid date."))
        ]
    if application_date > now:
        errors.append(make_import_error(gettext_noop("application_date cannot be in the future.")))

    decision_date = _optional_date(raw, "decision_date")
    activated_at = _optional_date(raw, "activated_at")
    expires_at = _optional_date(raw, "expires_at", end_of_day=True)
    completed_at = _optional_date(raw, "completed_at")
    rejection_reason = _clean(raw.get("rejection_reason"))
    admin_notes = _clean(raw.get("admin_notes"))
    enrollment_status_raw = _choice(raw.get("enrollment_status"))
    delivery_mode_raw = _choice(raw.get("delivery_mode"))

    if application_status == PROGRAM_APP_STATUS_REJECTED:
        if activated_at is not None:
            errors.append(
                make_import_error(gettext_noop("activated_at is only valid for an activated application."))
            )
        if expires_at is not None:
            errors.append(
                make_import_error(gettext_noop("expires_at is only valid for an activated application."))
            )
        if enrollment_status_raw:
            errors.append(
                make_import_error(
                    gettext_noop("enrollment_status is only valid for an activated application.")
                )
            )
        if completed_at is not None and program_type == PROGRAM_TYPE_COURSE:
            errors.append(
                make_import_error(
                    gettext_noop("completed_at is only valid for an activated course application.")
                )
            )
        if _clean(raw.get("entry_semester")):
            errors.append(
                make_import_error(
                    gettext_noop("entry_semester is only valid for an activated diploma application.")
                )
            )
        if not rejection_reason:
            errors.append(
                make_import_error(gettext_noop("rejection_reason is required for a rejected application."))
            )
        if not decision_date:
            decision_date = application_date
    else:
        if rejection_reason:
            errors.append(
                make_import_error(gettext_noop("rejection_reason is only valid for a rejected application."))
            )
        if activated_at is None:
            errors.append(
                make_import_error(gettext_noop("activated_at is required for an activated application."))
            )
        if decision_date is None:
            decision_date = activated_at

    if decision_date is not None and decision_date < application_date:
        errors.append(make_import_error(gettext_noop("decision_date cannot be before application_date.")))
    if activated_at is not None and activated_at < application_date:
        errors.append(make_import_error(gettext_noop("activated_at cannot be before application_date.")))
    if activated_at is not None and activated_at > now:
        errors.append(make_import_error(gettext_noop("activated_at cannot be in the future.")))
    if decision_date is not None and decision_date > now:
        errors.append(make_import_error(gettext_noop("decision_date cannot be in the future.")))
    if completed_at is not None:
        if completed_at > now:
            errors.append(make_import_error(gettext_noop("completed_at cannot be in the future.")))
        if activated_at is not None and completed_at < activated_at:
            errors.append(make_import_error(gettext_noop("completed_at cannot be before activated_at.")))
    if expires_at is not None and activated_at is not None and expires_at <= activated_at:
        errors.append(make_import_error(gettext_noop("expires_at must be after activated_at.")))

    if program_type == PROGRAM_TYPE_DIPLOMA:
        if any(
            _clean(raw.get(key))
            for key in ("course_title", "course_id", "course_version", "course_version_id")
        ):
            errors.append(
                make_import_error(gettext_noop("Course fields are not valid for a diploma application."))
            )
        if completed_at is not None:
            errors.append(
                make_import_error(gettext_noop("completed_at is only valid for a course application."))
            )
        cohort = _resolve_cohort(raw, indexes, errors)
        if errors and cohort is None:
            return None, errors
        assert cohort is not None
        entry_semester = _resolve_entry_semester(raw, cohort.diploma, indexes, errors)
        delivery_mode = delivery_mode_raw or DELIVERY_MODE_ONLINE
        if delivery_mode not in dict(DELIVERY_MODE_CHOICES):
            errors.append(make_import_error(gettext_noop("Invalid delivery_mode.")))
        course = None
        course_version = None
        if application_status == PROGRAM_APP_STATUS_REJECTED:
            enrollment_status = ""
        else:
            if expires_at is None and cohort.end_date is not None:
                expires_at = datetime.combine(
                    cohort.end_date,
                    datetime.max.time(),
                    tzinfo=timezone.get_current_timezone(),
                )
            enrollment_status = _derive_enrollment_status(
                enrollment_status_raw, expires_at=expires_at, completed_at=None, now=now
            )
            _validate_active_expiry(enrollment_status, expires_at, errors)
    else:
        if any(
            _clean(raw.get(key)) for key in ("diploma_title", "cohort_name", "cohort_id", "entry_semester")
        ):
            errors.append(
                make_import_error(gettext_noop("Diploma fields are not valid for a course application."))
            )
        course, course_version = _resolve_course(raw, indexes, errors)
        if errors and (course is None or course_version is None):
            return None, errors
        assert course is not None and course_version is not None
        cohort = None
        entry_semester = None
        delivery_mode = DELIVERY_MODE_ONLINE
        if (
            expires_at is None
            and activated_at is not None
            and course_version.access_duration_days is not None
        ):
            expires_at = activated_at + timedelta(days=course_version.access_duration_days)
        if application_status == PROGRAM_APP_STATUS_REJECTED:
            enrollment_status = ""
        else:
            enrollment_status = _derive_enrollment_status(
                enrollment_status_raw,
                expires_at=expires_at,
                completed_at=completed_at,
                now=now,
            )
            _validate_active_expiry(enrollment_status, expires_at, errors)

    if errors:
        return None, errors

    return (
        _RowPlan(
            student=student,
            program_type=program_type,
            cohort=cohort,
            course=course,
            course_version=course_version,
            application_status=application_status,
            application_date=application_date,
            decision_date=decision_date or application_date,
            activated_at=activated_at,
            expires_at=expires_at,
            completed_at=completed_at,
            enrollment_status=enrollment_status,
            entry_semester=entry_semester,
            delivery_mode=delivery_mode,
            rejection_reason=rejection_reason,
            admin_notes=admin_notes,
        ),
        [],
    )


def _derive_enrollment_status(
    raw_status: str,
    *,
    expires_at: datetime | None,
    completed_at: datetime | None,
    now: datetime,
) -> str:
    if raw_status:
        return raw_status
    if completed_at is not None:
        return ENROLLMENT_STATUS_COMPLETED
    if expires_at is not None and expires_at <= now:
        return ENROLLMENT_STATUS_EXPIRED
    return ENROLLMENT_STATUS_ACTIVE


def _validate_active_expiry(enrollment_status: str, expires_at: datetime | None, errors: list[dict]) -> None:
    allowed = {
        ENROLLMENT_STATUS_ACTIVE,
        ENROLLMENT_STATUS_COMPLETED,
        ENROLLMENT_STATUS_EXPIRED,
        ENROLLMENT_STATUS_RESTRICTED,
    }
    if enrollment_status not in allowed:
        errors.append(
            make_import_error(
                gettext_noop("enrollment_status must be active, completed, expired, or restricted.")
            )
        )
        return
    if (
        enrollment_status == ENROLLMENT_STATUS_ACTIVE
        and expires_at is not None
        and expires_at <= timezone.now()
    ):
        errors.append(
            make_import_error(
                gettext_noop("An active enrollment cannot have an expiry date in the past; use expired.")
            )
        )


def _build_indexes(student_ids: set[int]) -> _Indexes:
    cohorts = list(Cohort.objects.select_related("diploma").all())
    courses = list(Course.objects.all())
    versions = list(
        CourseVersion.objects.select_related("course").order_by("course_id", "-created_at", "-id")
    )
    semester_diploma_ids = {cohort.diploma_id for cohort in cohorts}
    semesters = list(
        Semester.objects.filter(diploma_id__in=semester_diploma_ids).order_by("diploma_id", "order")
    )

    cohort_by_id = {cohort.id: cohort for cohort in cohorts}
    cohort_by_key = {(cohort.diploma.title.casefold(), cohort.name.casefold()): cohort for cohort in cohorts}
    course_by_id = {course.id: course for course in courses}
    courses_by_title: dict[str, list[Course]] = {}
    for course in courses:
        courses_by_title.setdefault(course.title.casefold(), []).append(course)
    version_by_id = {version.id: version for version in versions}
    versions_by_course: dict[int, list[CourseVersion]] = {}
    for version in versions:
        versions_by_course.setdefault(version.course_id, []).append(version)
    semesters_by_diploma: dict[int, list[Semester]] = {}
    for semester in semesters:
        semesters_by_diploma.setdefault(semester.diploma_id, []).append(semester)

    existing_apps: dict[tuple[int, str, int], tuple[int, str]] = {}
    for app in ProgramApplication.objects.filter(student_id__in=student_ids).values(
        "id", "student_id", "program_type", "cohort_id", "course_id", "status"
    ):
        target_id = app["cohort_id"] if app["program_type"] == PROGRAM_TYPE_DIPLOMA else app["course_id"]
        if target_id is not None:
            existing_apps[_target_key(app["student_id"], app["program_type"], target_id)] = (
                app["id"],
                app["status"],
            )

    existing_diploma_enrollments = {
        (enrollment["student_id"], enrollment["cohort_id"])
        for enrollment in DiplomaEnrollment.objects.filter(student_id__in=student_ids).values(
            "student_id", "cohort_id"
        )
    }
    existing_course_enrollments = {
        (enrollment["student_id"], enrollment["course_version__course_id"])
        for enrollment in CourseEnrollment.objects.filter(student_id__in=student_ids).values(
            "student_id", "course_version__course_id"
        )
    }

    return _Indexes(
        cohort_by_id=cohort_by_id,
        cohort_by_key=cohort_by_key,
        course_by_id=course_by_id,
        courses_by_title=courses_by_title,
        version_by_id=version_by_id,
        versions_by_course=versions_by_course,
        semesters_by_diploma=semesters_by_diploma,
        existing_apps=existing_apps,
        existing_diploma_enrollments=existing_diploma_enrollments,
        existing_course_enrollments=existing_course_enrollments,
    )


def _match_rows(rows: list[ApplicationImportRow]):
    matcher = build_student_matcher([row.raw_data for row in rows])
    return {row.pk: matcher(row.raw_data) for row in rows}


def _existing_target_error(plan: _RowPlan, indexes: _Indexes) -> dict | None:
    target_id = plan.cohort.id if plan.program_type == PROGRAM_TYPE_DIPLOMA else plan.course.id
    existing = indexes.existing_apps.get(_target_key(plan.student.id, plan.program_type, target_id))
    if existing is not None:
        app_id, status = existing
        return make_import_error(
            gettext_noop(
                "This student already has an application for the same program "
                "(application #%(id)s, status: %(status)s). Review it manually."
            ),
            id=app_id,
            status_code=status,
        )
    if plan.program_type == PROGRAM_TYPE_DIPLOMA:
        if (plan.student.id, plan.cohort.id) in indexes.existing_diploma_enrollments:
            return make_import_error(
                gettext_noop(
                    "This student already has an enrollment for the same cohort. Review it manually."
                )
            )
    elif (plan.student.id, plan.course.id) in indexes.existing_course_enrollments:
        return make_import_error(
            gettext_noop("This student already has an enrollment for the same course. Review it manually.")
        )
    return None


def _row_error(row: ApplicationImportRow, message: str) -> str:
    return _("Row %(row)d: %(message)s") % {"row": row.row_number, "message": message}


@transaction.atomic
def preview_application_import(import_id: int) -> ApplicationImport:
    """Resolve and validate every row without writing application data."""
    batch = ApplicationImport.objects.select_for_update().get(pk=import_id)
    if batch.status in (
        APPLICATION_IMPORT_STATUS_PREVIEWED,
        APPLICATION_IMPORT_STATUS_FAILED,
        APPLICATION_IMPORT_STATUS_CONFIRMED,
    ):
        return batch
    if batch.status != APPLICATION_IMPORT_STATUS_PENDING:
        raise ValueError(_("Import is not ready for preview."))

    rows = list(batch.rows.order_by("row_number"))
    if not rows:
        raise ValueError(_("The CSV contains no data rows."))

    matches = _match_rows(rows)
    student_ids = {student.id for student, _method, _error in matches.values() if student}
    indexes = _build_indexes(student_ids)
    seen_targets: dict[tuple[int, str, int], ApplicationImportRow] = {}

    for row in rows:
        row.resolved_cohort = None
        row.resolved_course_version = None
        row.resolved_entry_semester = None
        row.resolved_entry_semester_ref = None
        row.errors = []
        student, method, match_error = matches[row.pk]
        row.match_method = method or MATCH_METHOD_NONE

        if student is None:
            row.matched_student = None
            row.status = (
                APPLICATION_IMPORT_ROW_AMBIGUOUS
                if method == MATCH_METHOD_AMBIGUOUS
                else APPLICATION_IMPORT_ROW_FAILED
            )
            error = (
                match_error.as_dict()
                if match_error is not None and method == MATCH_METHOD_AMBIGUOUS
                else make_import_error(gettext_noop("No existing student matched. Import the student first."))
            )
            row.errors = [error]
            row.error_message = render_import_errors(row.errors)
            continue

        row.matched_student = student
        plan, plan_errors = _parse_plan(row.raw_data, student, indexes)
        if plan is None:
            row.status = APPLICATION_IMPORT_ROW_FAILED
            row.errors = plan_errors
            row.error_message = render_import_errors(plan_errors)
            continue

        row.resolved_cohort = plan.cohort
        row.resolved_course_version = plan.course_version
        row.resolved_entry_semester = (
            plan.entry_semester
            if plan.program_type == PROGRAM_TYPE_DIPLOMA
            and plan.application_status == PROGRAM_APP_STATUS_ACTIVATED
            else None
        )
        row.resolved_entry_semester_ref = (
            plan.entry_semester.id
            if plan.entry_semester is not None
            and plan.program_type == PROGRAM_TYPE_DIPLOMA
            and plan.application_status == PROGRAM_APP_STATUS_ACTIVATED
            else None
        )
        target_id = plan.cohort.id if plan.program_type == PROGRAM_TYPE_DIPLOMA else plan.course.id
        target_key = _target_key(student.id, plan.program_type, target_id)
        previous_row = seen_targets.get(target_key)
        if previous_row is not None:
            error = make_import_error(
                gettext_noop("Rows %(first)d and %(second)d target the same student and program."),
                first=previous_row.row_number,
                second=row.row_number,
            )
            previous_row.status = APPLICATION_IMPORT_ROW_AMBIGUOUS
            previous_row.errors = [error]
            previous_row.error_message = render_import_errors([error])
            row.status = APPLICATION_IMPORT_ROW_AMBIGUOUS
            row.errors = [error]
            row.error_message = render_import_errors([error])
            continue
        seen_targets[target_key] = row

        existing_error = _existing_target_error(plan, indexes)
        if existing_error:
            row.status = APPLICATION_IMPORT_ROW_FAILED
            row.errors = [existing_error]
            row.error_message = render_import_errors([existing_error])
            continue

        row.status = APPLICATION_IMPORT_ROW_MATCHED
        row.error_message = ""

    now = timezone.now()
    for row in rows:
        row.updated_at = now
    batch.rows.bulk_update(
        rows,
        [
            "matched_student",
            "match_method",
            "resolved_cohort",
            "resolved_course_version",
            "resolved_entry_semester",
            "resolved_entry_semester_ref",
            "status",
            "errors",
            "error_message",
            "updated_at",
        ],
    )

    matched = sum(1 for row in rows if row.status == APPLICATION_IMPORT_ROW_MATCHED)
    failed = sum(
        1 for row in rows if row.status in (APPLICATION_IMPORT_ROW_FAILED, APPLICATION_IMPORT_ROW_AMBIGUOUS)
    )
    error_rows = [row.row_number for row in rows if row.errors]
    batch.total_rows = len(rows)
    batch.matched_rows = matched
    batch.failed_rows = failed
    batch.status = APPLICATION_IMPORT_STATUS_PREVIEWED if failed == 0 else APPLICATION_IMPORT_STATUS_FAILED
    batch.error_rows = error_rows
    batch.error_summary = ""
    batch.save(
        update_fields=[
            "total_rows",
            "matched_rows",
            "failed_rows",
            "status",
            "error_rows",
            "error_summary",
            "updated_at",
        ]
    )

    record_audit_event(
        actor=batch.uploaded_by,
        action=ACTION_APPLICATION_IMPORT_PREVIEWED,
        target_type="ApplicationImport",
        target_id=str(batch.id),
        section=SECTION_ADMISSIONS,
        target_label=batch.original_filename,
        metadata={
            "total": batch.total_rows,
            "matched": matched,
            "failed": failed,
            "error_rows": error_rows,
        },
    )
    return batch


@transaction.atomic
def confirm_application_import(import_id: int, confirmed_by) -> ApplicationImport:
    """Apply a fully validated historical application CSV atomically."""
    batch = ApplicationImport.objects.select_for_update().get(pk=import_id)
    if batch.status == APPLICATION_IMPORT_STATUS_CONFIRMED:
        return batch
    if batch.status != APPLICATION_IMPORT_STATUS_PREVIEWED:
        raise ValueError(_("Upload is not in preview state."))

    # Serialize identity mutations while the historical rows are re-matched
    # and written. The normal student import and profile updates take the same
    # transaction-scoped lock.
    lock_identity_writes()

    rows = list(
        batch.rows.select_for_update().filter(status=APPLICATION_IMPORT_ROW_MATCHED).order_by("row_number")
    )
    if len(rows) != batch.total_rows:
        raise ValueError(_("Some rows are not valid. Preview the file again."))

    matches = _match_rows(rows)
    student_ids = {student.id for student, _method, _error in matches.values() if student}
    if student_ids:
        # Live application submission locks the same StudentProfile row before
        # its duplicate check. Locking every matched student here (in ID order)
        # serializes import-vs-live creation for the same identity and keeps
        # the existing-target check below authoritative.
        locked_student_ids = set(
            StudentProfile.objects.select_for_update()
            .filter(id__in=student_ids)
            .order_by("id")
            .values_list("id", flat=True)
        )
        if locked_student_ids != student_ids:
            raise ValueError(_("One or more matched students changed after preview. Upload the file again."))
    indexes = _build_indexes(student_ids)
    plans: list[tuple[ApplicationImportRow, _RowPlan]] = []
    errors: list[str] = []

    for row in rows:
        student, _method, match_error = matches[row.pk]
        if student is None or student.id != row.matched_student_id:
            errors.append(
                _row_error(
                    row,
                    str(match_error)
                    if match_error is not None
                    else _("The student match changed after preview. Upload the file again."),
                )
            )
            continue
        plan, plan_errors = _parse_plan(row.raw_data, student, indexes)
        if plan is None:
            errors.append(_row_error(row, render_import_errors(plan_errors)))
            continue
        if plan.cohort is not None and plan.cohort.id != row.resolved_cohort_id:
            errors.append(
                _row_error(row, _("The diploma target changed after preview. Upload the file again."))
            )
            continue
        if plan.course_version is not None and plan.course_version.id != row.resolved_course_version_id:
            errors.append(
                _row_error(row, _("The course target changed after preview. Upload the file again."))
            )
            continue
        preview_semester_id = plan.entry_semester.id if plan.entry_semester is not None else None
        if (
            plan.program_type == PROGRAM_TYPE_DIPLOMA
            and plan.application_status == PROGRAM_APP_STATUS_ACTIVATED
            and preview_semester_id != row.resolved_entry_semester_ref
        ):
            errors.append(
                _row_error(
                    row,
                    _("The diploma entry semester changed after preview. Upload the file again."),
                )
            )
            continue
        existing_error = _existing_target_error(plan, indexes)
        if existing_error:
            errors.append(_row_error(row, render_import_errors([existing_error])))
            continue
        plans.append((row, plan))

    if errors:
        raise ValueError(_("Import validation failed:\n%(errors)s") % {"errors": "\n".join(errors)})

    now = timezone.now()
    applications = [
        ProgramApplication(
            student=plan.student,
            program_type=plan.program_type,
            delivery_mode=plan.delivery_mode,
            cohort=plan.cohort,
            course=plan.course,
            status=plan.application_status,
            admin_notes=plan.admin_notes,
            rejection_reason=plan.rejection_reason,
            reviewed_by=None,
            reviewed_at=plan.decision_date,
            created_at=plan.application_date,
        )
        for _row, plan in plans
    ]
    try:
        ProgramApplication.objects.bulk_create(applications, batch_size=500)
    except IntegrityError:
        raise ValueError(
            _("The preview is stale because an application was created concurrently. Upload the file again.")
        ) from None

    diploma_enrollments: list[tuple[ApplicationImportRow, _RowPlan, DiplomaEnrollment]] = []
    course_enrollments: list[tuple[ApplicationImportRow, _RowPlan, CourseEnrollment]] = []
    for (row, plan), _application in zip(plans, applications, strict=True):
        if plan.application_status != PROGRAM_APP_STATUS_ACTIVATED:
            continue
        if plan.program_type == PROGRAM_TYPE_DIPLOMA:
            diploma_enrollments.append(
                (
                    row,
                    plan,
                    DiplomaEnrollment(
                        student=plan.student,
                        cohort=plan.cohort,
                        status=plan.enrollment_status,
                        activated_at=plan.activated_at,
                        expires_at=plan.expires_at,
                        entry_semester=plan.entry_semester,
                    ),
                )
            )
        else:
            course_enrollments.append(
                (
                    row,
                    plan,
                    CourseEnrollment(
                        student=plan.student,
                        course_version=plan.course_version,
                        status=plan.enrollment_status,
                        activated_at=plan.activated_at,
                        expires_at=plan.expires_at,
                        completed_at=plan.completed_at,
                    ),
                )
            )

    if diploma_enrollments:
        DiplomaEnrollment.objects.bulk_create(
            [enrollment for _row, _plan, enrollment in diploma_enrollments],
            batch_size=500,
        )
    if course_enrollments:
        CourseEnrollment.objects.bulk_create(
            [enrollment for _row, _plan, enrollment in course_enrollments],
            batch_size=500,
        )

    diploma_by_row = {row.pk: enrollment for row, _plan, enrollment in diploma_enrollments}
    course_by_row = {row.pk: enrollment for row, _plan, enrollment in course_enrollments}
    enrollment_by_row: dict[int, DiplomaEnrollment | CourseEnrollment] = {
        **diploma_by_row,
        **course_by_row,
    }

    decisions = []
    for (row, plan), application in zip(plans, applications, strict=True):
        enrollment = enrollment_by_row.get(row.pk)
        enrollment_type = ""
        if (
            plan.program_type == PROGRAM_TYPE_DIPLOMA
            and plan.application_status == PROGRAM_APP_STATUS_ACTIVATED
        ):
            enrollment_type = "diploma"
        elif (
            plan.program_type == PROGRAM_TYPE_COURSE
            and plan.application_status == PROGRAM_APP_STATUS_ACTIVATED
        ):
            enrollment_type = "course"

        metadata = {
            "source": "historical_import",
            "import_id": batch.id,
            "import_row_id": row.id,
            "original_application_date": plan.application_date.isoformat(),
            "original_decision_date": plan.decision_date.isoformat(),
            "enrollment_type": enrollment_type,
            "enrollment_id": enrollment.id if enrollment is not None else None,
            "enrollment_status": plan.enrollment_status,
        }
        decisions.append(
            ProgramApplicationDecision(
                program_application=application,
                action=(
                    DECISION_ACTION_ACTIVATE
                    if plan.application_status == PROGRAM_APP_STATUS_ACTIVATED
                    else DECISION_ACTION_REJECT
                ),
                old_status="",
                new_status=plan.application_status,
                actor=confirmed_by,
                reason=(
                    plan.rejection_reason if plan.application_status == PROGRAM_APP_STATUS_REJECTED else ""
                ),
                metadata=metadata,
            )
        )
    if decisions:
        ProgramApplicationDecision.objects.bulk_create(decisions, batch_size=500)

    active_enrollments = [
        enrollment
        for row, plan in plans
        if plan.application_status == PROGRAM_APP_STATUS_ACTIVATED
        and plan.enrollment_status == ENROLLMENT_STATUS_ACTIVE
        and (enrollment := enrollment_by_row.get(row.pk)) is not None
    ]
    if active_enrollments:
        get_or_create_attendance_codes_for_enrollments(
            active_enrollments,
            actor=confirmed_by,
        )

    for (row, _plan), application in zip(plans, applications, strict=True):
        row.created_application = application
        row.created_diploma_enrollment = diploma_by_row.get(row.pk)
        row.created_course_enrollment = course_by_row.get(row.pk)
        row.status = APPLICATION_IMPORT_ROW_CREATED
        row.errors = []
        row.error_message = ""
        row.updated_at = now
    if rows:
        batch.rows.bulk_update(
            rows,
            [
                "created_application",
                "created_diploma_enrollment",
                "created_course_enrollment",
                "status",
                "errors",
                "error_message",
                "updated_at",
            ],
        )

    batch.created_rows = len(applications)
    batch.created_diploma_enrollments = len(diploma_enrollments)
    batch.created_course_enrollments = len(course_enrollments)
    batch.status = APPLICATION_IMPORT_STATUS_CONFIRMED
    batch.completed_at = now
    batch.error_rows = []
    batch.error_summary = ""
    batch.save(
        update_fields=[
            "created_rows",
            "created_diploma_enrollments",
            "created_course_enrollments",
            "status",
            "completed_at",
            "error_rows",
            "error_summary",
            "updated_at",
        ]
    )

    record_audit_event(
        actor=confirmed_by,
        action=ACTION_APPLICATION_IMPORT_CONFIRMED,
        target_type="ApplicationImport",
        target_id=str(batch.id),
        section=SECTION_ADMISSIONS,
        target_label=batch.original_filename,
        metadata={
            "total": batch.total_rows,
            "created_applications": batch.created_rows,
            "created_diploma_enrollments": batch.created_diploma_enrollments,
            "created_course_enrollments": batch.created_course_enrollments,
        },
    )
    return batch
