"""Tests for exam code generation, roster management, and offline grade entry (T5)."""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import Cohort, Course, CourseVersion, Diploma, DiplomaEnrollment, DiplomaLecture, Semester
from apps.applications.constants import PROGRAM_APP_STATUS_ACCEPTED, PROGRAM_TYPE_DIPLOMA
from apps.applications.models import ProgramApplication
from apps.accounts.constants import GROUP_ADMIN, GROUP_STUDENT
from apps.accounts.models import User
from apps.assessments.constants import (
    ASSESSMENT_TYPE_QUIZ,
    ASSESSMENT_TYPE_SEMESTER_EXAM,
    ATTEMPT_STATUS_GRADED,
    DELIVERY_MODE_OFFLINE,
    DELIVERY_MODE_ONLINE,
)
from apps.assessments.models import (
    Assessment,
    AssessmentLectureSection,
    OfflineSectionGrade,
    SemesterExamCode,
)
from apps.assessments.services import (
    can_take_diploma_assessment,
    enter_offline_section_grade,
    finalize_offline_attempt,
    generate_exam_codes,
    change_exam_code_delivery_mode,
)
from apps.students.models import StudentProfile


def _create_profile(user: User, **kwargs) -> StudentProfile:
    return StudentProfile.objects.create(
        user=user,
        full_name=kwargs.get("full_name", "Test Student"),
        date_of_birth=date(2000, 1, 1),
        gender="male",
        address="Address",
        phone=kwargs.get("phone", "01000000001"),
        national_id=kwargs.get("national_id", "10000000000001"),
        education="bachelor",
        specialization="Spec",
        employer="Employer",
        current_job="Job",
        church="Church",
        priest_name="Priest",
        priest_contact="01000000000",
    )


class GenerateExamCodesTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.diploma = Diploma.objects.create(title="D", total_semesters=2, created_by=self.admin)
        self.cohort = Cohort.objects.create(diploma=self.diploma, name="C1", start_date=date(2026, 1, 1), created_by=self.admin)
        self.semester = Semester.objects.create(diploma=self.diploma, title="Sem1", order=1)
        self.student1 = User.objects.create_user(email="s1@test.com", password="x")
        self.student2 = User.objects.create_user(email="s2@test.com", password="x")
        _create_profile(self.student1, phone="01000000002", national_id="10000000000002")
        _create_profile(self.student2, phone="01000000003", national_id="10000000000003")
        DiplomaEnrollment.objects.create(student=self.student1.student_profile, cohort=self.cohort, status=ENROLLMENT_STATUS_ACTIVE)
        DiplomaEnrollment.objects.create(student=self.student2.student_profile, cohort=self.cohort, status=ENROLLMENT_STATUS_ACTIVE)
        now = timezone.now()
        self.assessment = Assessment.objects.create(
            title="Sem Exam", assessment_type=ASSESSMENT_TYPE_SEMESTER_EXAM,
            semester=self.semester, cohort=self.cohort,
            open_at=now - timezone.timedelta(days=1), close_at=now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"), created_by=self.admin,
        )

    def test_generates_one_code_per_active_enrollment(self):
        count = generate_exam_codes(self.assessment, self.admin)
        self.assertEqual(count, 2)
        self.assertEqual(SemesterExamCode.objects.filter(assessment=self.assessment).count(), 2)

    def test_idempotent(self):
        generate_exam_codes(self.assessment, self.admin)
        count = generate_exam_codes(self.assessment, self.admin)
        self.assertEqual(count, 0)
        self.assertEqual(SemesterExamCode.objects.filter(assessment=self.assessment).count(), 2)

    def test_codes_are_unique_per_assessment(self):
        generate_exam_codes(self.assessment, self.admin)
        codes = SemesterExamCode.objects.filter(assessment=self.assessment).values_list("code_number", flat=True)
        self.assertEqual(len(set(codes)), len(codes))

    def test_raises_for_non_semester_exam(self):
        course = Course.objects.create(title="Course", created_by=self.admin)
        course_version = CourseVersion.objects.create(course=course, label="v1", created_by=self.admin)
        quiz = Assessment.objects.create(
            title="Quiz", assessment_type=ASSESSMENT_TYPE_QUIZ,
            course_version=course_version,
            open_at=timezone.now(), close_at=timezone.now() + timezone.timedelta(days=1),
            passing_score=Decimal("50.00"), created_by=self.admin,
        )
        with self.assertRaises(ValueError):
            generate_exam_codes(quiz, self.admin)

    def test_raises_without_cohort(self):
        no_cohort = Assessment.objects.create(
            title="No Cohort", assessment_type=ASSESSMENT_TYPE_SEMESTER_EXAM,
            open_at=timezone.now(), close_at=timezone.now() + timezone.timedelta(days=1),
            passing_score=Decimal("50.00"), created_by=self.admin,
            legacy_status="legacy_archived",
        )
        with self.assertRaises(ValueError):
            generate_exam_codes(no_cohort, self.admin)


class OfflineToggleTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.diploma = Diploma.objects.create(title="D", total_semesters=2, created_by=self.admin)
        self.cohort = Cohort.objects.create(diploma=self.diploma, name="C1", start_date=date(2026, 1, 1), created_by=self.admin)
        self.semester = Semester.objects.create(diploma=self.diploma, title="Sem1", order=1)
        self.student = User.objects.create_user(email="s@test.com", password="x")
        _create_profile(self.student)
        de = DiplomaEnrollment.objects.create(student=self.student.student_profile, cohort=self.cohort, status=ENROLLMENT_STATUS_ACTIVE)
        ProgramApplication.objects.create(
            student=self.student.student_profile,
            program_type=PROGRAM_TYPE_DIPLOMA,
            cohort=self.cohort,
            status=PROGRAM_APP_STATUS_ACCEPTED,
        )
        now = timezone.now()
        self.assessment = Assessment.objects.create(
            title="Sem Exam", assessment_type=ASSESSMENT_TYPE_SEMESTER_EXAM,
            semester=self.semester, cohort=self.cohort,
            open_at=now - timezone.timedelta(days=1), close_at=now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"), created_by=self.admin,
        )
        generate_exam_codes(self.assessment, self.admin)
        self.code = SemesterExamCode.objects.filter(assessment=self.assessment).first()

    def test_toggle_to_offline(self):
        result = change_exam_code_delivery_mode(self.code.id, DELIVERY_MODE_OFFLINE, self.admin)
        self.assertEqual(result.delivery_mode, DELIVERY_MODE_OFFLINE)

    def test_toggle_to_online(self):
        change_exam_code_delivery_mode(self.code.id, DELIVERY_MODE_OFFLINE, self.admin)
        result = change_exam_code_delivery_mode(self.code.id, DELIVERY_MODE_ONLINE, self.admin)
        self.assertEqual(result.delivery_mode, DELIVERY_MODE_ONLINE)


class OfflineGradeEntryTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.grader = User.objects.create_user(email="grader@test.com", password="x")
        self.diploma = Diploma.objects.create(title="D", total_semesters=2, created_by=self.admin)
        self.cohort = Cohort.objects.create(diploma=self.diploma, name="C1", start_date=date(2026, 1, 1), created_by=self.admin)
        self.semester = Semester.objects.create(diploma=self.diploma, title="Sem1", order=1)
        self.lecture = DiplomaLecture.objects.create(cohort=self.cohort, semester=self.semester, order=1, title="L1")
        self.student = User.objects.create_user(email="s@test.com", password="x")
        _create_profile(self.student)
        de = DiplomaEnrollment.objects.create(student=self.student.student_profile, cohort=self.cohort, status=ENROLLMENT_STATUS_ACTIVE)
        ProgramApplication.objects.create(
            student=self.student.student_profile,
            program_type=PROGRAM_TYPE_DIPLOMA,
            cohort=self.cohort,
            delivery_mode=DELIVERY_MODE_OFFLINE,
            status=PROGRAM_APP_STATUS_ACCEPTED,
        )
        now = timezone.now()
        self.assessment = Assessment.objects.create(
            title="Sem Exam", assessment_type=ASSESSMENT_TYPE_SEMESTER_EXAM,
            semester=self.semester, cohort=self.cohort,
            open_at=now - timezone.timedelta(days=1), close_at=now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"), total_points=Decimal("100.00"), created_by=self.admin,
        )
        self.section = AssessmentLectureSection.objects.create(
            assessment=self.assessment, lecture=self.lecture, order=1, mcq_count=0, max_grade=Decimal("100.00"),
        )
        generate_exam_codes(self.assessment, self.admin)
        self.code = SemesterExamCode.objects.filter(assessment=self.assessment).first()

    def test_enter_offline_grade(self):
        grade = enter_offline_section_grade(self.code.id, self.section.id, Decimal("85.00"), self.grader)
        self.assertEqual(grade.score, Decimal("85.00"))
        self.assertEqual(grade.graded_by, self.grader)

    def test_grade_exceeds_max_raises(self):
        with self.assertRaises(ValueError):
            enter_offline_section_grade(self.code.id, self.section.id, Decimal("101.00"), self.grader)

    def test_finalize_creates_graded_attempt(self):
        enter_offline_section_grade(self.code.id, self.section.id, Decimal("85.00"), self.grader)
        attempt = finalize_offline_attempt(self.code.id)
        self.assertEqual(attempt.status, ATTEMPT_STATUS_GRADED)
        self.assertEqual(attempt.score_points, Decimal("85.00"))

    def test_finalize_missing_sections_not_graded(self):
        lecture2 = DiplomaLecture.objects.create(cohort=self.cohort, semester=self.semester, order=2, title="L2")
        section2 = AssessmentLectureSection.objects.create(
            assessment=self.assessment, lecture=lecture2, order=2, mcq_count=0, max_grade=Decimal("50.00"),
        )
        enter_offline_section_grade(self.code.id, self.section.id, Decimal("85.00"), self.grader)
        with self.assertRaises(ValueError):
            finalize_offline_attempt(self.code.id)


class CanTakeDiplomaAssessmentTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.student = User.objects.create_user(email="s@test.com", password="x")
        _create_profile(self.student)
        self.diploma = Diploma.objects.create(title="D", total_semesters=2, created_by=self.admin)
        self.cohort = Cohort.objects.create(diploma=self.diploma, name="C1", start_date=date(2026, 1, 1), created_by=self.admin)
        self.semester = Semester.objects.create(diploma=self.diploma, title="Sem1", order=1)
        DiplomaEnrollment.objects.create(student=self.student.student_profile, cohort=self.cohort, status=ENROLLMENT_STATUS_ACTIVE)
        ProgramApplication.objects.create(
            student=self.student.student_profile,
            program_type=PROGRAM_TYPE_DIPLOMA,
            cohort=self.cohort,
            status=PROGRAM_APP_STATUS_ACCEPTED,
        )
        now = timezone.now()
        self.assessment = Assessment.objects.create(
            title="Sem Exam", assessment_type=ASSESSMENT_TYPE_SEMESTER_EXAM,
            semester=self.semester, cohort=self.cohort,
            open_at=now - timezone.timedelta(days=1), close_at=now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"), created_by=self.admin,
        )

    def test_online_student_can_take(self):
        generate_exam_codes(self.assessment, self.admin)
        self.assertTrue(can_take_diploma_assessment(self.student, self.assessment))

    def test_offline_student_cannot_take(self):
        generate_exam_codes(self.assessment, self.admin)
        code = SemesterExamCode.objects.filter(assessment=self.assessment).first()
        change_exam_code_delivery_mode(code.id, DELIVERY_MODE_OFFLINE, self.admin)
        self.assertFalse(can_take_diploma_assessment(self.student, self.assessment))

    def test_no_active_enrollment_returns_false(self):
        outsider = User.objects.create_user(email="outsider@test.com", password="x")
        _create_profile(outsider, phone="01000000999", national_id="10000000000999")
        self.assertFalse(can_take_diploma_assessment(outsider, self.assessment))


class RosterViewAccessTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.student = User.objects.create_user(email="student@test.com", password="x")
        group, _ = Group.objects.get_or_create(name=GROUP_STUDENT)
        self.student.groups.add(group)
        self.diploma = Diploma.objects.create(title="D", total_semesters=2, created_by=self.admin)
        self.cohort = Cohort.objects.create(diploma=self.diploma, name="C1", start_date=date(2026, 1, 1), created_by=self.admin)
        self.semester = Semester.objects.create(diploma=self.diploma, title="Sem1", order=1)
        now = timezone.now()
        self.assessment = Assessment.objects.create(
            title="Sem Exam", assessment_type=ASSESSMENT_TYPE_SEMESTER_EXAM,
            semester=self.semester, cohort=self.cohort,
            open_at=now - timezone.timedelta(days=1), close_at=now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"), created_by=self.admin,
        )
        self.roster_url = reverse("assessments:admin_roster", args=[self.assessment.id])

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(self.roster_url)
        self.assertEqual(response.status_code, 302)

    def test_student_cannot_access_roster(self):
        self.client.force_login(self.student)
        response = self.client.get(self.roster_url)
        self.assertEqual(response.status_code, 403)

    def test_admin_can_access_roster(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.roster_url)
        self.assertEqual(response.status_code, 200)


class OfflineExportTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.diploma = Diploma.objects.create(title="D", total_semesters=2, created_by=self.admin)
        self.cohort = Cohort.objects.create(diploma=self.diploma, name="C1", start_date=date(2026, 1, 1), created_by=self.admin)
        self.semester = Semester.objects.create(diploma=self.diploma, title="Sem1", order=1)
        self.student = User.objects.create_user(email="s@test.com", password="x")
        _create_profile(self.student, full_name="Offline Student")
        de = DiplomaEnrollment.objects.create(student=self.student.student_profile, cohort=self.cohort, status=ENROLLMENT_STATUS_ACTIVE)
        ProgramApplication.objects.create(
            student=self.student.student_profile,
            program_type=PROGRAM_TYPE_DIPLOMA,
            cohort=self.cohort,
            status=PROGRAM_APP_STATUS_ACCEPTED,
        )
        now = timezone.now()
        self.assessment = Assessment.objects.create(
            title="Sem Exam", assessment_type=ASSESSMENT_TYPE_SEMESTER_EXAM,
            semester=self.semester, cohort=self.cohort,
            open_at=now - timezone.timedelta(days=1), close_at=now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"), created_by=self.admin,
        )
        generate_exam_codes(self.assessment, self.admin)
        code = SemesterExamCode.objects.filter(assessment=self.assessment).first()
        change_exam_code_delivery_mode(code.id, DELIVERY_MODE_OFFLINE, self.admin)

    def test_export_returns_text(self):
        self.client.force_login(self.admin)
        url = reverse("assessments:admin_export_offline_codes", args=[self.assessment.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Offline Student")
