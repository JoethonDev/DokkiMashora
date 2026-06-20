"""Tests for student dashboard selectors."""

from __future__ import annotations

from datetime import date, timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.academics.constants import (
    ENROLLMENT_STATUS_ACTIVE,
    ENROLLMENT_STATUS_EXPIRED,
    ENROLLMENT_STATUS_PENDING,
)
from apps.academics.models import (
    Cohort,
    Course,
    CourseEnrollment,
    CourseVersion,
    Diploma,
    DiplomaEnrollment,
    Semester,
)
from apps.accounts.models import User
from apps.applications.models import Application, ProgramApplication
from apps.assessments.constants import ASSESSMENT_TYPE_QUIZ
from apps.assessments.models import Assessment, AssessmentAttempt
from apps.certificates.models import GeneratedCertificate
from apps.communications.email_message import EmailMessage
from apps.entrance_exams.models import EntranceAttempt, EntranceExam
from apps.progression.constants import (
    CERTIFICATE_STATUS_AVAILABLE,
    CERTIFICATE_STATUS_REVOKED,
    PROMOTION_DECISION_AUTO_PROMOTE,
)
from apps.progression.models import (
    FinalExamEligibility,
    PromotionDecision,
    StudentDiplomaCompletion,
    StudentSemesterProgress,
)
from apps.students.constants import EDUCATION_BACHELOR, GENDER_MALE
from apps.students.models import StudentProfile
from apps.students.selectors import get_dashboard_data


def _create_student(email="student@example.com") -> tuple[User, StudentProfile]:
    user = User.objects.create_user(email=email, password="x")
    profile = StudentProfile.objects.create(
        user=user,
        full_name="Test Student",
        date_of_birth=date(2000, 1, 1),
        gender=GENDER_MALE,
        address="Cairo",
        phone="01000000000",
        national_id="29001010100011",
        education=EDUCATION_BACHELOR,
    )
    return user, profile


def _create_diploma_cohort(admin):
    diploma = Diploma.objects.create(title="Test Diploma", created_by=admin)
    cohort = Cohort.objects.create(
        diploma=diploma, name="C2025",
        start_date=date(2025, 9, 1), created_by=admin,
    )
    return diploma, cohort


class GetDashboardDataStructureTests(TestCase):
    def setUp(self) -> None:
        _, self.profile = _create_student()

    def test_returns_all_expected_keys(self) -> None:
        data = get_dashboard_data(self.profile)
        expected_keys = {
            "student", "user", "identity", "applications",
            "diploma_enrollments", "course_enrollments",
            "active_courses", "expired_courses",
            "exam_assignments", "assessment_attempts",
            "homework_submissions", "semester_progress",
            "promotion_decisions", "completions",
            "certificates", "eligibilities",
            "payments", "notifications", "reapply",
        }
        self.assertEqual(set(data.keys()), expected_keys)

    def test_identity_contains_all_fields(self) -> None:
        data = get_dashboard_data(self.profile)
        identity = data["identity"]
        for key in ("full_name", "email", "phone", "national_id",
                     "date_of_birth", "age", "gender", "address",
                     "education", "specialization", "employer",
                     "current_job", "church", "priest_name", "priest_contact"):
            self.assertIn(key, identity)

    def test_empty_lists_when_no_related_data(self) -> None:
        data = get_dashboard_data(self.profile)
        for section in ("applications", "diploma_enrollments", "course_enrollments",
                         "active_courses", "expired_courses", "exam_assignments",
                         "assessment_attempts", "homework_submissions",
                         "semester_progress", "promotion_decisions", "completions",
                         "certificates", "eligibilities", "notifications"):
            self.assertEqual(data[section], [])
        self.assertEqual(data["payments"], {"total_paid": 0, "record_count": 0, "records": []})


class GetDashboardDataApplicationsTests(TestCase):
    def setUp(self) -> None:
        self.user, self.profile = _create_student()

    def test_returns_applications_ordered_by_created_at_desc(self) -> None:
        root = Application.objects.create(student=self.profile)
        pa1 = ProgramApplication.objects.create(
            student=self.profile, root_application=root, program_type="diploma",
        )
        ProgramApplication.objects.filter(pk=pa1.pk).update(created_at=timezone.now() - timedelta(hours=1))
        pa2 = ProgramApplication.objects.create(
            student=self.profile, root_application=root, program_type="diploma",
        )
        data = get_dashboard_data(self.profile)
        self.assertEqual(len(data["applications"]), 2)
        self.assertEqual(data["applications"][0], pa2)
        self.assertEqual(data["applications"][1], pa1)


class GetDashboardDataCourseEnrollmentsTests(TestCase):
    def setUp(self) -> None:
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.user, self.profile = _create_student()
        self.diploma = Diploma.objects.create(title="Test Diploma", created_by=self.admin)
        self.semester = Semester.objects.create(
            diploma=self.diploma, order=1, title="Sem 1",
        )
        self.cohort = Cohort.objects.create(
            diploma=self.diploma, name="C2025",
            start_date=date(2025, 9, 1), created_by=self.admin,
        )
        self.course = Course.objects.create(title="Test Course", created_by=self.admin)
        self.version = CourseVersion.objects.create(
            course=self.course, cohort=self.cohort, semester=self.semester,
            is_active=True, created_by=self.admin,
        )

    def test_active_courses_returns_only_active_non_expired(self) -> None:
        now = timezone.now()
        CourseEnrollment.objects.create(
            student=self.profile,
            course_version=self.version,
            status=ENROLLMENT_STATUS_ACTIVE,
            activated_at=now,
            expires_at=now + timedelta(days=30),
        )
        data = get_dashboard_data(self.profile)
        self.assertEqual(len(data["active_courses"]), 1)
        self.assertEqual(data["active_courses"][0]["course"], self.course)

    def test_expired_courses_returns_expired_enrollments(self) -> None:
        now = timezone.now()
        CourseEnrollment.objects.create(
            student=self.profile,
            course_version=self.version,
            status=ENROLLMENT_STATUS_EXPIRED,
            activated_at=now - timedelta(days=60),
            expires_at=now - timedelta(days=30),
        )
        data = get_dashboard_data(self.profile)
        self.assertEqual(len(data["expired_courses"]), 1)
        self.assertEqual(data["expired_courses"][0]["enrollment"].status, ENROLLMENT_STATUS_EXPIRED)

    def test_active_courses_excludes_expired_enrollments(self) -> None:
        now = timezone.now()
        CourseEnrollment.objects.create(
            student=self.profile,
            course_version=self.version,
            status=ENROLLMENT_STATUS_EXPIRED,
            activated_at=now - timedelta(days=60),
            expires_at=now - timedelta(days=30),
        )
        data = get_dashboard_data(self.profile)
        self.assertEqual(len(data["active_courses"]), 0)

    def test_diploma_enrollments_returns_all(self) -> None:
        DiplomaEnrollment.objects.create(
            student=self.profile,
            cohort=self.cohort,
            status=ENROLLMENT_STATUS_PENDING,
        )
        data = get_dashboard_data(self.profile)
        self.assertEqual(len(data["diploma_enrollments"]), 1)

    def test_course_enrollments_returns_all_statuses(self) -> None:
        now = timezone.now()
        course2 = Course.objects.create(title="Course 2", created_by=self.admin)
        version2 = CourseVersion.objects.create(
            course=course2, cohort=self.cohort, semester=self.semester,
            is_active=True, created_by=self.admin,
        )
        CourseEnrollment.objects.create(
            student=self.profile,
            course_version=self.version,
            status=ENROLLMENT_STATUS_PENDING,
        )
        CourseEnrollment.objects.create(
            student=self.profile,
            course_version=version2,
            status=ENROLLMENT_STATUS_ACTIVE,
            activated_at=now,
            expires_at=now + timedelta(days=30),
        )
        data = get_dashboard_data(self.profile)
        self.assertEqual(len(data["course_enrollments"]), 2)


class GetDashboardDataExamAssignmentsTests(TestCase):
    def setUp(self) -> None:
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.user, self.profile = _create_student()
        self.root = Application.objects.create(student=self.profile)
        _, self.cohort = _create_diploma_cohort(self.admin)
        self.prog_app = ProgramApplication.objects.create(
            student=self.profile, root_application=self.root,
            cohort=self.cohort, program_type="diploma",
        )
        self.entrance_exam = EntranceExam.objects.create(
            title="Entrance Exam",
            diploma=self.cohort.diploma, cohort=self.cohort,
            duration_minutes=60, passing_score=50,
            is_active=True,
            open_at=timezone.now(), close_at=timezone.now() + timedelta(hours=2),
            created_by=self.admin,
        )

    def test_returns_assignments(self) -> None:
        EntranceAttempt.objects.create(
            exam=self.entrance_exam, student=self.profile,
            program_application=self.prog_app,
        )
        data = get_dashboard_data(self.profile)
        self.assertEqual(len(data["exam_assignments"]), 1)


class GetDashboardDataCertificatesTests(TestCase):
    def setUp(self) -> None:
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.user, self.profile = _create_student()
        self.diploma = Diploma.objects.create(title="D", created_by=self.admin)
        self.cohort = Cohort.objects.create(
            diploma=self.diploma, name="C",
            start_date=date(2025, 9, 1), created_by=self.admin,
        )
        self.enrollment = DiplomaEnrollment.objects.create(
            student=self.profile, cohort=self.cohort, status=ENROLLMENT_STATUS_ACTIVE,
        )

    def test_returns_only_available_certificates(self) -> None:
        available = GeneratedCertificate.objects.create(
            student=self.profile,
            diploma_enrollment=self.enrollment,
            status=CERTIFICATE_STATUS_AVAILABLE,
            certificate_number="CERT-001",
        )
        GeneratedCertificate.objects.create(
            student=self.profile,
            diploma_enrollment=self.enrollment,
            status=CERTIFICATE_STATUS_REVOKED,
            certificate_number="CERT-002",
        )
        data = get_dashboard_data(self.profile)
        self.assertEqual(len(data["certificates"]), 1)
        self.assertEqual(data["certificates"][0], available)

    def test_returns_empty_when_no_available_certificates(self) -> None:
        GeneratedCertificate.objects.create(
            student=self.profile,
            diploma_enrollment=self.enrollment,
            status=CERTIFICATE_STATUS_REVOKED,
            certificate_number="CERT-003",
        )
        data = get_dashboard_data(self.profile)
        self.assertEqual(data["certificates"], [])


class GetDashboardDataNotificationsTests(TestCase):
    def setUp(self) -> None:
        self.user, self.profile = _create_student()

    def test_returns_email_messages(self) -> None:
        EmailMessage.objects.create(
            to_email=self.user.email,
            subject="Welcome",
            body="Hello",
        )
        data = get_dashboard_data(self.profile)
        self.assertEqual(len(data["notifications"]), 1)

    def test_excludes_other_users_emails(self) -> None:
        EmailMessage.objects.create(to_email="other@example.com", subject="Nope", body="Nope")
        data = get_dashboard_data(self.profile)
        self.assertEqual(data["notifications"], [])


class GetDashboardDataProgressionTests(TestCase):
    def setUp(self) -> None:
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.user, self.profile = _create_student()
        self.diploma = Diploma.objects.create(title="D", created_by=self.admin)
        self.cohort = Cohort.objects.create(
            diploma=self.diploma, name="C",
            start_date=date(2025, 9, 1), created_by=self.admin,
        )
        self.enrollment = DiplomaEnrollment.objects.create(
            student=self.profile, cohort=self.cohort, status=ENROLLMENT_STATUS_ACTIVE,
        )
        self.semester = Semester.objects.create(
            diploma=self.diploma, order=1, title="Sem 1",
        )

    def test_semester_progress_is_ordered(self) -> None:
        StudentSemesterProgress.objects.create(
            student=self.profile, semester=self.semester,
            cohort=self.cohort,
            diploma_enrollment=self.enrollment, calculated_score=3.0,
        )
        data = get_dashboard_data(self.profile)
        self.assertEqual(len(data["semester_progress"]), 1)

    def test_promotion_decisions_returns_decisions(self) -> None:
        PromotionDecision.objects.create(
            student=self.profile,
            diploma_enrollment=self.enrollment,
            decision_type=PROMOTION_DECISION_AUTO_PROMOTE,
        )
        data = get_dashboard_data(self.profile)
        self.assertEqual(len(data["promotion_decisions"]), 1)

    def test_completions_returns_completions(self) -> None:
        StudentDiplomaCompletion.objects.create(
            student=self.profile,
            diploma_enrollment=self.enrollment,
            final_score=3.5,
        )
        data = get_dashboard_data(self.profile)
        self.assertEqual(len(data["completions"]), 1)

    def test_eligibilities_returns_eligibilities(self) -> None:
        FinalExamEligibility.objects.create(
            student=self.profile,
            diploma_enrollment=self.enrollment,
            eligible=True,
        )
        data = get_dashboard_data(self.profile)
        self.assertEqual(len(data["eligibilities"]), 1)


class GetDashboardDataExamScopingTests(TestCase):
    def setUp(self) -> None:
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.user, self.profile = _create_student()
        self.root = Application.objects.create(student=self.profile)
        _, self.cohort = _create_diploma_cohort(self.admin)
        self.prog_app = ProgramApplication.objects.create(
            student=self.profile, root_application=self.root,
            cohort=self.cohort, program_type="diploma",
        )
        self.entrance_exam = EntranceExam.objects.create(
            title="Entrance Exam",
            diploma=self.cohort.diploma,
            cohort=self.cohort,
            duration_minutes=60,
            passing_score=50,
            is_active=True,
            open_at=timezone.now(),
            close_at=timezone.now() + timedelta(hours=2),
            created_by=self.admin,
        )

    def test_returns_entrance_attempts(self) -> None:
        EntranceAttempt.objects.create(
            exam=self.entrance_exam, student=self.profile,
            program_application=self.prog_app,
        )
        data = get_dashboard_data(self.profile)
        self.assertEqual(len(data["exam_assignments"]), 1)

    def test_excludes_other_student_attempts(self) -> None:
        other_user = User.objects.create_user(email="other@test.com", password="x")
        other_profile = StudentProfile.objects.create(
            user=other_user, full_name="Other",
            date_of_birth=date(2000, 1, 1), gender=GENDER_MALE,
            address="Giza", phone="01111111111", national_id="29001010100022",
            education=EDUCATION_BACHELOR,
        )
        other_root = Application.objects.create(student=other_profile)
        other_prog_app = ProgramApplication.objects.create(
            student=other_profile, root_application=other_root,
            cohort=self.cohort, program_type="diploma",
        )
        EntranceAttempt.objects.create(
            exam=self.entrance_exam, student=other_profile,
            program_application=other_prog_app,
        )
        data = get_dashboard_data(self.profile)
        self.assertEqual(data["exam_assignments"], [])


class GetDashboardDataHiddenAssessmentTests(TestCase):
    def setUp(self) -> None:
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.user, self.profile = _create_student()
        self.assessment = Assessment.objects.create(
            title="Test Assessment", assessment_type=ASSESSMENT_TYPE_QUIZ,
            created_by=self.admin,
            open_at=timezone.now(), close_at=timezone.now() + timedelta(hours=2),
            passing_score=50,
        )

    def test_returns_attempts_regardless_of_visibility(self) -> None:
        """The selector returns all attempts; templates gate on visible_to_student."""
        AssessmentAttempt.objects.create(
            assessment=self.assessment, student=self.user,
            visible_to_student=False, attempt_number=1,
        )
        data = get_dashboard_data(self.profile)
        self.assertEqual(len(data["assessment_attempts"]), 1)
        self.assertFalse(data["assessment_attempts"][0].visible_to_student)

    def test_excludes_other_student_attempts(self) -> None:
        other_user = User.objects.create_user(email="other@test.com", password="x")
        AssessmentAttempt.objects.create(
            assessment=self.assessment, student=other_user,
            visible_to_student=True, attempt_number=1,
        )
        data = get_dashboard_data(self.profile)
        self.assertEqual(data["assessment_attempts"], [])


class GetDashboardDataQueryCountTests(TestCase):
    """Ensure the dashboard does not produce N+1 queries."""

    def setUp(self) -> None:
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.user, self.profile = _create_student()
        self.diploma = Diploma.objects.create(title="D", created_by=self.admin)
        self.cohort = Cohort.objects.create(
            diploma=self.diploma, name="C",
            start_date=date(2025, 9, 1), created_by=self.admin,
        )
        self.semester = Semester.objects.create(
            diploma=self.diploma, order=1, title="Sem 1",
        )
        self.course = Course.objects.create(title="Test Course", created_by=self.admin)
        self.version = CourseVersion.objects.create(
            course=self.course, cohort=self.cohort, semester=self.semester,
            is_active=True, created_by=self.admin,
        )
        now = timezone.now()
        CourseEnrollment.objects.create(
            student=self.profile, course_version=self.version,
            status=ENROLLMENT_STATUS_ACTIVE,
            activated_at=now, expires_at=now + timedelta(days=30),
        )
        Application.objects.create(student=self.profile)
        DiplomaEnrollment.objects.create(
            student=self.profile, cohort=self.cohort, status=ENROLLMENT_STATUS_ACTIVE,
        )
        assessment = Assessment.objects.create(
            title="A1", assessment_type=ASSESSMENT_TYPE_QUIZ,
            created_by=self.admin,
            open_at=timezone.now(), close_at=timezone.now() + timedelta(hours=2),
            passing_score=50,
        )
        AssessmentAttempt.objects.create(
            assessment=assessment, student=self.user,
            visible_to_student=False, attempt_number=1,
        )

    def test_dashboard_query_count_bounded(self) -> None:
        with CaptureQueriesContext(connection) as captured:
            get_dashboard_data(self.profile)
        query_count = len(captured.captured_queries)
        self.assertLess(query_count, 25, f"Dashboard used {query_count} queries; expected < 25")
