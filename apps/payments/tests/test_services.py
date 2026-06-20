"""Tests for payment services: matching, parsing, upload confirm rollback."""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from django.contrib.auth.models import Group
from django.db import IntegrityError
from django.test import TestCase

from apps.accounts.constants import GROUP_STUDENT
from apps.accounts.models import User
from apps.students.models import StudentProfile

from ..constants import (
    MATCH_METHOD_AMBIGUOUS,
    MATCH_METHOD_EMAIL,
    MATCH_METHOD_NATIONAL_ID,
    MATCH_METHOD_NONE,
    MATCH_METHOD_PHONE,
    PAYMENT_STATUS_PAID,
    PAYMENT_STATUS_UNPAID,
    ROW_STATUS_FAILED,
    ROW_STATUS_MATCHED,
    ROW_STATUS_PARSED,
    UPLOAD_STATUS_CONFIRMED,
    UPLOAD_STATUS_PREVIEWED,
)
from ..models import PaymentRecord, PaymentUpload, PaymentUploadRow
from ..services import (
    confirm_upload,
    create_upload,
    match_student,
    parse_row_data,
    preview_upload,
    validate_csv_content,
)


class MatchStudentTests(TestCase):
    def setUp(self) -> None:
        self.student_group, _ = Group.objects.get_or_create(name=GROUP_STUDENT)
        self.user1 = User.objects.create_user(email="s1@example.com", password="pass")
        self.user1.groups.add(self.student_group)
        self.profile1 = StudentProfile.objects.create(
            user=self.user1,
            full_name="Student One",
            national_id="12345678901234",
            phone="+201234567890",
            date_of_birth=date(2000, 1, 1),
        )
        self.user2 = User.objects.create_user(email="s2@example.com", password="pass")
        self.user2.groups.add(self.student_group)
        self.profile2 = StudentProfile.objects.create(
            user=self.user2,
            full_name="Student Two",
            national_id="98765432109876",
            phone="+201111111111",
            date_of_birth=date(2000, 1, 2),
        )

    def test_match_by_national_id(self) -> None:
        student, method, error = match_student({
            "national_id": "12345678901234",
            "email": "",
            "phone": "",
        })
        self.assertEqual(student, self.profile1)
        self.assertEqual(method, MATCH_METHOD_NATIONAL_ID)
        self.assertEqual(error, "")

    def test_match_by_email(self) -> None:
        student, method, error = match_student({
            "national_id": "",
            "email": "s2@example.com",
            "phone": "",
        })
        self.assertEqual(student, self.profile2)
        self.assertEqual(method, MATCH_METHOD_EMAIL)
        self.assertEqual(error, "")

    def test_match_by_phone(self) -> None:
        student, method, error = match_student({
            "national_id": "",
            "email": "",
            "phone": "+201234567890",
        })
        self.assertEqual(student, self.profile1)
        self.assertEqual(method, MATCH_METHOD_PHONE)
        self.assertEqual(error, "")

    def test_no_match_returns_none_method(self) -> None:
        student, method, error = match_student({
            "national_id": "00000000000000",
            "email": "nonexistent@example.com",
            "phone": "+209999999999",
        })
        self.assertIsNone(student)
        self.assertEqual(method, MATCH_METHOD_NONE)
        self.assertIn("No student found matching any identifier", error)

    def test_conflicting_identifiers_different_students(self) -> None:
        """National ID matches student1 but email matches student2."""
        student, method, error = match_student({
            "national_id": "12345678901234",
            "email": "s2@example.com",
            "phone": "",
        })
        self.assertIsNone(student)
        self.assertEqual(method, MATCH_METHOD_AMBIGUOUS)
        self.assertIn("different students", error)

    def test_national_id_and_email_same_student(self) -> None:
        """When both identifiers point to the same student, match succeeds."""
        student, method, error = match_student({
            "national_id": "12345678901234",
            "email": "s1@example.com",
            "phone": "",
        })
        self.assertEqual(student, self.profile1)
        self.assertEqual(method, MATCH_METHOD_NATIONAL_ID)
        self.assertEqual(error, "")


class ParseRowDataTests(TestCase):
    def test_parses_valid_row(self) -> None:
        result = parse_row_data({
            "national_id": "12345678901234",
            "amount": "1500.00",
            "payment_date": "2026-01-15",
            "external_reference": "INV-001",
        }, 1)
        self.assertEqual(result["status"], ROW_STATUS_PARSED)
        self.assertEqual(result["amount"], Decimal("1500.00"))
        self.assertEqual(result["external_reference"], "INV-001")

    def test_fails_on_invalid_amount(self) -> None:
        result = parse_row_data({
            "national_id": "12345678901234",
            "amount": "not-a-number",
            "payment_date": "2026-01-15",
        }, 1)
        self.assertEqual(result["status"], ROW_STATUS_FAILED)

    def test_fails_on_empty_national_id(self) -> None:
        result = parse_row_data({
            "national_id": "",
            "amount": "500",
            "payment_date": "2026-01-15",
        }, 1)
        self.assertEqual(result["status"], ROW_STATUS_FAILED)
        self.assertIn("national ID", result["error_message"])

    def test_fails_on_invalid_date(self) -> None:
        result = parse_row_data({
            "national_id": "12345678901234",
            "amount": "500",
            "payment_date": "bad-date",
        }, 1)
        self.assertEqual(result["status"], ROW_STATUS_FAILED)
        self.assertIn("Invalid date", result["error_message"])


class ConfirmUploadRollbackTests(TestCase):
    def setUp(self) -> None:
        self.student_group, _ = Group.objects.get_or_create(name=GROUP_STUDENT)
        self.admin_user = User.objects.create_user(
            email="admin@example.com", password="pass", is_staff=True
        )
        self.student_user = User.objects.create_user(
            email="stud@example.com", password="pass"
        )
        self.student_user.groups.add(self.student_group)
        self.profile = StudentProfile.objects.create(
            user=self.student_user,
            full_name="Test Student",
            national_id="11111111111111",
            phone="+201000000000",
            date_of_birth=date(2000, 1, 1),
        )
        self.upload = PaymentUpload.objects.create(
            uploaded_by=self.admin_user,
            original_filename="test.csv",
            status=UPLOAD_STATUS_PREVIEWED,
            matched_rows=1,
        )
        self.row = PaymentUploadRow.objects.create(
            upload=self.upload,
            row_number=1,
            raw_data={"national_id": "11111111111111", "amount": "1000"},
            normalized_national_id="11111111111111",
            amount=Decimal("1000.00"),
            currency="EGP",
            status=ROW_STATUS_MATCHED,
            matched_student=self.profile,
        )

    def test_confirm_rolls_back_on_creation_failure(self) -> None:
        """If a row fails during confirm, the entire transaction rolls back."""
        self.row.matched_student = None
        self.row.save(update_fields=["matched_student"])
        with self.assertRaises(ValueError):
            confirm_upload(self.upload.id, confirmed_by=self.admin_user)
        self.upload.refresh_from_db()
        self.assertEqual(self.upload.status, UPLOAD_STATUS_PREVIEWED)
        self.assertEqual(PaymentRecord.objects.count(), 0)

    def test_confirm_success_creates_records(self) -> None:
        result = confirm_upload(self.upload.id, confirmed_by=self.admin_user)
        self.assertEqual(result.status, UPLOAD_STATUS_CONFIRMED)
        self.assertEqual(PaymentRecord.objects.count(), 1)
        record = PaymentRecord.objects.first()
        self.assertEqual(record.student, self.profile)
        self.assertEqual(record.amount, Decimal("1000.00"))
        self.assertEqual(record.status, PAYMENT_STATUS_PAID)


class UniqueReferenceConstraintTests(TestCase):
    def setUp(self) -> None:
        self.student_group, _ = Group.objects.get_or_create(name=GROUP_STUDENT)
        self.admin_user = User.objects.create_user(
            email="admin2@example.com", password="pass", is_staff=True
        )
        self.s1_user = User.objects.create_user(email="s1u@example.com", password="pass")
        self.s1_user.groups.add(self.student_group)
        self.s1 = StudentProfile.objects.create(
            user=self.s1_user, full_name="S1", national_id="21111111111111",
            phone="+201111111112",
            date_of_birth=date(2000, 1, 1),
        )
        self.s2_user = User.objects.create_user(email="s2u@example.com", password="pass")
        self.s2_user.groups.add(self.student_group)
        self.s2 = StudentProfile.objects.create(
            user=self.s2_user, full_name="S2", national_id="31111111111111",
            phone="+201111111113",
            date_of_birth=date(2000, 1, 2),
        )

    def test_same_external_reference_for_different_students_raises(self) -> None:
        PaymentRecord.objects.create(
            student=self.s1,
            external_reference="REF-001",
            amount=Decimal("500"),
            status=PAYMENT_STATUS_PAID,
        )
        with self.assertRaises(IntegrityError):
            PaymentRecord.objects.create(
                student=self.s2,
                external_reference="REF-001",
                amount=Decimal("500"),
                status=PAYMENT_STATUS_PAID,
            )

    def test_empty_external_reference_allows_duplicates(self) -> None:
        PaymentRecord.objects.create(
            student=self.s1,
            external_reference="",
            amount=Decimal("100"),
            status=PAYMENT_STATUS_PAID,
        )
        PaymentRecord.objects.create(
            student=self.s2,
            external_reference="",
            amount=Decimal("200"),
            status=PAYMENT_STATUS_PAID,
        )
        self.assertEqual(PaymentRecord.objects.count(), 2)


class PaymentSelectorQueryCountTests(TestCase):
    """Verify payment selectors produce bounded queries."""

    def setUp(self) -> None:
        from apps.accounts.constants import GROUP_STUDENT
        group, _ = Group.objects.get_or_create(name=GROUP_STUDENT)
        self.user = User.objects.create_user(email="s@test.com", password="x")
        self.user.groups.add(group)
        self.student = StudentProfile.objects.create(
            user=self.user, full_name="S",
            national_id="12345678901234", phone="+201234567890",
            date_of_birth=date(2000, 1, 1),
        )
        for i in range(3):
            PaymentRecord.objects.create(
                student=self.student,
                external_reference=f"REF-{i}",
                amount=Decimal("100"),
                status=PAYMENT_STATUS_PAID,
            )

    def test_get_upload_queryset_query_count_bounded(self) -> None:
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from ..selectors import get_upload_queryset
        with CaptureQueriesContext(connection) as captured:
            list(get_upload_queryset())
        self.assertLess(len(captured.captured_queries), 5,
                        f"get_upload_queryset used {len(captured.captured_queries)} queries; expected < 5")

    def test_get_student_payment_summary_query_count_bounded(self) -> None:
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from ..selectors import get_student_payment_summary
        with CaptureQueriesContext(connection) as captured:
            get_student_payment_summary(self.student)
        self.assertLess(len(captured.captured_queries), 5,
                        f"get_student_payment_summary used {len(captured.captured_queries)} queries; expected < 5")


class PaymentFilterSelectorTests(TestCase):
    """Tests for payment list filter selectors."""

    def setUp(self) -> None:
        from apps.accounts.constants import GROUP_ADMIN, GROUP_STUDENT
        from apps.accounts.models import User as UserModel
        self.student_group, _ = Group.objects.get_or_create(name=GROUP_STUDENT)
        self.admin_group, _ = Group.objects.get_or_create(name=GROUP_ADMIN)
        self.admin = UserModel.objects.create_user(
            email="admin@test.com", password="pass", is_staff=True
        )
        self.admin.groups.add(self.admin_group)
        self.user = UserModel.objects.create_user(email="stu@test.com", password="x")
        self.user.groups.add(self.student_group)
        self.student = StudentProfile.objects.create(
            user=self.user, full_name="Test Student",
            national_id="11111111111111", phone="+201000000000",
            date_of_birth=date(2000, 1, 1),
        )
        self.upload = PaymentUpload.objects.create(
            uploaded_by=self.admin, original_filename="f.csv",
            status=UPLOAD_STATUS_PREVIEWED, matched_rows=1,
        )
        PaymentRecord.objects.create(
            student=self.student, upload_row=None,
            external_reference="REF-F1", amount=Decimal("500"),
            status=PAYMENT_STATUS_PAID, paid_at=datetime(2026, 1, 15, tzinfo=UTC),
        )

    def test_get_upload_queryset_filters_by_status(self) -> None:
        from ..selectors import get_upload_queryset
        qs = get_upload_queryset(status=UPLOAD_STATUS_PREVIEWED)
        self.assertEqual(qs.count(), 1)
        qs = get_upload_queryset(status=UPLOAD_STATUS_CONFIRMED)
        self.assertEqual(qs.count(), 0)

    def test_get_upload_queryset_filters_by_date_range(self) -> None:
        from ..selectors import get_upload_queryset
        qs = get_upload_queryset(date_from="2026-01-01", date_to="2026-12-31")
        self.assertEqual(qs.count(), 1)
        qs = get_upload_queryset(date_from="2025-01-01", date_to="2025-12-31")
        self.assertEqual(qs.count(), 0)

    def test_get_payment_record_queryset_filters_by_status(self) -> None:
        from ..selectors import get_payment_record_queryset
        qs = get_payment_record_queryset(status=PAYMENT_STATUS_PAID)
        self.assertEqual(qs.count(), 1)
        qs = get_payment_record_queryset(status=PAYMENT_STATUS_UNPAID)
        self.assertEqual(qs.count(), 0)

    def test_get_payment_record_queryset_searches_by_name(self) -> None:
        from ..selectors import get_payment_record_queryset
        qs = get_payment_record_queryset(student_q="Test")
        self.assertEqual(qs.count(), 1)
        qs = get_payment_record_queryset(student_q="Nonexistent")
        self.assertEqual(qs.count(), 0)

    def test_get_payment_record_queryset_searches_by_national_id(self) -> None:
        from ..selectors import get_payment_record_queryset
        qs = get_payment_record_queryset(student_q="11111111111111")
        self.assertEqual(qs.count(), 1)

    def test_get_payment_record_queryset_searches_by_phone(self) -> None:
        from ..selectors import get_payment_record_queryset
        qs = get_payment_record_queryset(student_q="01000000000")
        self.assertEqual(qs.count(), 1)

    def test_get_payment_record_queryset_searches_by_email(self) -> None:
        from ..selectors import get_payment_record_queryset
        qs = get_payment_record_queryset(student_q="stu@test.com")
        self.assertEqual(qs.count(), 1)

    def test_get_payment_record_queryset_filters_by_date_range(self) -> None:
        from ..selectors import get_payment_record_queryset
        qs = get_payment_record_queryset(date_from="2026-01-01", date_to="2026-01-31")
        self.assertEqual(qs.count(), 1)
        qs = get_payment_record_queryset(date_from="2025-01-01", date_to="2025-12-31")
        self.assertEqual(qs.count(), 0)


class EnrollmentLinkingTests(TestCase):
    """Tests that preview_upload and confirm_upload populate enrollment FKs."""

    def setUp(self) -> None:
        from apps.academics.models import (
            Cohort,
            Course,
            CourseEnrollment,
            CourseVersion,
            Diploma,
            DiplomaEnrollment,
        )
        from apps.accounts.constants import GROUP_ADMIN, GROUP_STUDENT
        from apps.accounts.models import User as UserModel
        from apps.applications.models import Application

        self.student_group, _ = Group.objects.get_or_create(name=GROUP_STUDENT)
        admin_group, _ = Group.objects.get_or_create(name=GROUP_ADMIN)
        self.admin = UserModel.objects.create_user(
            email="admin@enroll.com", password="pass", is_staff=True
        )
        self.admin.groups.add(admin_group)
        self.user = UserModel.objects.create_user(
            email="enroll-stu@test.com", password="pass"
        )
        self.user.groups.add(self.student_group)
        self.student = StudentProfile.objects.create(
            user=self.user, full_name="Enroll Student",
            national_id="22222222222222", phone="+201222222222",
            date_of_birth=date(2000, 2, 2),
        )
        diploma = Diploma.objects.create(title="Test Diploma", created_by=self.admin)
        cohort = Cohort.objects.create(
            diploma=diploma, name="Cohort 1",
            start_date=date(2026, 1, 1), created_by=self.admin,
        )
        self.diploma_enrollment = DiplomaEnrollment.objects.create(
            student=self.student, cohort=cohort,
        )
        course = Course.objects.create(
            title="Test Course", created_by=self.admin,
        )
        course_version = CourseVersion.objects.create(
            course=course, cohort=cohort, created_by=self.admin,
        )
        self.course_enrollment = CourseEnrollment.objects.create(
            student=self.student, course_version=course_version,
        )
        self.application = Application.objects.create(
            student=self.student, cohort=cohort,
        )

    def _create_previewed_upload(self, raw_data: dict) -> PaymentUpload:
        upload = PaymentUpload.objects.create(
            uploaded_by=self.admin, original_filename="enroll.csv",
            total_rows=1,
        )
        PaymentUploadRow.objects.create(
            upload=upload, row_number=1,
            raw_data=raw_data,
            normalized_national_id="22222222222222",
            amount=Decimal("2000"), currency="EGP",
            status=ROW_STATUS_PARSED,
        )
        return preview_upload(upload.id)

    def test_preview_links_diploma_enrollment(self) -> None:
        upload = self._create_previewed_upload({
            "national_id": "22222222222222",
            "amount": "2000",
            "payment_date": "2026-03-01",
            "diploma_enrollment_id": str(self.diploma_enrollment.id),
        })
        row = upload.rows.first()
        self.assertEqual(row.matched_diploma_enrollment, self.diploma_enrollment)
        self.assertIsNone(row.matched_course_enrollment)

    def test_preview_links_course_enrollment(self) -> None:
        upload = self._create_previewed_upload({
            "national_id": "22222222222222",
            "amount": "1500",
            "payment_date": "2026-03-01",
            "course_enrollment_id": str(self.course_enrollment.id),
        })
        row = upload.rows.first()
        self.assertEqual(row.matched_course_enrollment, self.course_enrollment)
        self.assertIsNone(row.matched_diploma_enrollment)

    def test_preview_links_application(self) -> None:
        upload = self._create_previewed_upload({
            "national_id": "22222222222222",
            "amount": "1000",
            "payment_date": "2026-03-01",
            "application_id": str(self.application.id),
        })
        row = upload.rows.first()
        self.assertEqual(row.matched_application, self.application)

    def test_preview_ignores_invalid_enrollment_id(self) -> None:
        upload = self._create_previewed_upload({
            "national_id": "22222222222222",
            "amount": "2000",
            "payment_date": "2026-03-01",
            "diploma_enrollment_id": "999999",
        })
        row = upload.rows.first()
        self.assertIsNone(row.matched_diploma_enrollment)

    def test_confirm_copies_enrollment_to_payment_record(self) -> None:
        upload = self._create_previewed_upload({
            "national_id": "22222222222222",
            "amount": "2000",
            "payment_date": "2026-03-15",
            "diploma_enrollment_id": str(self.diploma_enrollment.id),
        })
        upload.status = UPLOAD_STATUS_PREVIEWED
        upload.matched_rows = 1
        upload.save(update_fields=["status", "matched_rows"])
        result = confirm_upload(upload.id, confirmed_by=self.admin)
        record = result.rows.first().payment_records.first()
        self.assertEqual(record.diploma_enrollment, self.diploma_enrollment)
        self.assertIsNone(record.course_enrollment)


class CsvValidationTests(TestCase):
    """Tests for CSV content validation."""

    def test_valid_csv_parses_cleanly(self) -> None:
        content = b"national_id,amount,payment_date\n12345678901234,1000,2026-01-15\n"
        rows = validate_csv_content(content)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["national_id"], "12345678901234")

    def test_blank_optional_fields_do_not_crash(self) -> None:
        """csv.DictReader yields None for missing trailing columns; (v or '').strip() must handle it."""
        content = b"national_id,amount,payment_date,email,phone,notes\n12345678901234,1000,2026-01-15,,\n"
        rows = validate_csv_content(content)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["email"], "")
        self.assertEqual(rows[0]["phone"], "")
        self.assertEqual(rows[0]["notes"], "")

    def test_missing_optional_columns_do_not_crash(self) -> None:
        """CSV with only required columns must not raise."""
        content = b"national_id,amount,payment_date\n12345678901234,1000,2026-01-15\n"
        rows = validate_csv_content(content)
        self.assertEqual(len(rows), 1)


class CreateUploadAtomicTests(TestCase):
    """create_upload must not leave orphan rows when CSV is invalid."""

    def setUp(self) -> None:
        from apps.accounts.constants import GROUP_ADMIN
        admin_group, _ = Group.objects.get_or_create(name=GROUP_ADMIN)
        self.admin = User.objects.create_user(
            email="upload-admin@test.com", password="pass", is_staff=True
        )
        self.admin.groups.add(admin_group)

    def test_missing_headers_raises_before_upload_created(self) -> None:
        content = b"name,amount\nJohn,100\n"
        with self.assertRaises(ValueError):
            create_upload("bad.csv", content, uploaded_by=self.admin)
        self.assertEqual(PaymentUpload.objects.count(), 0)

    def test_empty_file_raises_before_upload_created(self) -> None:
        content = b""
        with self.assertRaises(ValueError):
            create_upload("empty.csv", content, uploaded_by=self.admin)
        self.assertEqual(PaymentUpload.objects.count(), 0)

    def test_valid_csv_creates_upload_and_rows(self) -> None:
        content = b"national_id,amount,payment_date\n12345678901234,1000,2026-01-15\n"
        upload = create_upload("good.csv", content, uploaded_by=self.admin)
        self.assertEqual(PaymentUpload.objects.count(), 1)
        self.assertEqual(upload.rows.count(), 1)
        self.assertEqual(upload.total_rows, 1)


class SafeDateStrTests(TestCase):
    """Tests for safe_date_str helper."""

    def test_valid_date_passes_through(self) -> None:
        from apps.core.permissions import safe_date_str
        self.assertEqual(safe_date_str("2026-06-20"), "2026-06-20")

    def test_invalid_date_returns_none(self) -> None:
        from apps.core.permissions import safe_date_str
        self.assertIsNone(safe_date_str("not-a-date"))
        self.assertIsNone(safe_date_str("2026-13-01"))
        self.assertIsNone(safe_date_str(""))
        self.assertIsNone(safe_date_str(None))
