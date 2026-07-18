"""Tests for the academics app models."""

from datetime import date, timedelta

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.academics.constants import (
    ENROLLMENT_STATUS_ACTIVE,
    ENROLLMENT_STATUS_PENDING,
    SCOPE_DIPLOMA_COHORT,
)
from apps.academics.models import (
    Cohort,
    Course,
    CourseEnrollment,
    CourseVersion,
    Diploma,
    DiplomaEnrollment,
    DiplomaLecture,
    DiplomaLectureWatchProgress,
    Lecture,
    ModeratorStudentAssignment,
    Semester,
    TeacherAssignment,
)
from apps.academics.services import (
    assign_moderator_to_students,
    assign_teacher_to_cohort,
    revoke_moderator_student_assignment,
)
from apps.accounts.models import User
from apps.students.constants import EDUCATION_BACHELOR, GENDER_MALE
from apps.students.models import StudentProfile


class DiplomaTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.diploma = Diploma.objects.create(
            title="Diploma in Theology",
            description="A theological program",
            total_semesters=4,
            created_by=self.admin,
        )

    def test_str(self):
        self.assertEqual(str(self.diploma), "Diploma in Theology")

    def test_default_is_active(self):
        self.assertTrue(self.diploma.is_active)


class SemesterTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.diploma = Diploma.objects.create(title="D", total_semesters=2, created_by=self.admin)
        self.semester = Semester.objects.create(diploma=self.diploma, order=1, title="Semester 1")

    def test_str_includes_diploma(self):
        self.assertIn(self.diploma.title, str(self.semester))
        self.assertIn("Semester 1", str(self.semester))

    def test_unique_order_per_diploma(self):
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            Semester.objects.create(diploma=self.diploma, order=1, title="Duplicate")


class CourseTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.course = Course.objects.create(title="Greek 101", created_by=self.admin)

    def test_str(self):
        self.assertEqual(str(self.course), "Greek 101")


class CohortTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.diploma = Diploma.objects.create(title="D", total_semesters=2, created_by=self.admin)
        self.cohort = Cohort.objects.create(
            diploma=self.diploma,
            name="2026 A",
            start_date=date(2026, 1, 1),
            created_by=self.admin,
        )

    def test_str(self):
        self.assertIn(self.diploma.title, str(self.cohort))
        self.assertIn("2026 A", str(self.cohort))

    def test_unique_name_per_diploma(self):
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            Cohort.objects.create(
                diploma=self.diploma,
                name="2026 A",
                start_date=date(2026, 6, 1),
                created_by=self.admin,
            )


class CourseVersionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.course = Course.objects.create(title="C1", created_by=self.admin)
        self.version = CourseVersion.objects.create(
            course=self.course,
            label="v1",
            access_duration_days=365,
            created_by=self.admin,
        )

    def test_str_standalone(self):
        self.assertIn(self.course.title, str(self.version))
        self.assertIn("v1", str(self.version))

    def test_str_with_cohort(self):
        diploma = Diploma.objects.create(title="D", total_semesters=2, created_by=self.admin)
        cohort = Cohort.objects.create(diploma=diploma, name="C1", start_date=date(2026, 1, 1), created_by=self.admin)
        version = CourseVersion.objects.create(
            course=self.course, label="v2", created_by=self.admin,
        )
        self.assertIn("C1", str(version))


class LectureTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.course = Course.objects.create(title="C1", created_by=self.admin)
        self.version = CourseVersion.objects.create(course=self.course, label="v1", created_by=self.admin)
        self.lecture = Lecture.objects.create(
            course_version=self.version,
            order=1,
            title="Intro",
            is_published=True,
        )

    def test_str(self):
        self.assertIn("Intro", str(self.lecture))

    def test_is_released_true_when_published_no_release_at(self):
        self.assertTrue(self.lecture.is_released)

    def test_is_released_false_when_not_published(self):
        self.lecture.is_published = False
        self.assertFalse(self.lecture.is_released)

    def test_is_released_false_before_release_at(self):
        self.lecture.release_at = timezone.now() + timedelta(days=1)
        self.assertFalse(self.lecture.is_released)

    def test_is_released_true_after_release_at(self):
        self.lecture.release_at = timezone.now() - timedelta(hours=1)
        self.assertTrue(self.lecture.is_released)

    def test_ordering(self):
        Lecture.objects.create(
            course_version=self.version, order=2, title="Second", is_published=True,
        )
        lectures = list(self.version.lectures.all())
        self.assertEqual(lectures[0].order, 1)
        self.assertEqual(lectures[1].order, 2)

    def test_unique_order_per_version(self):
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            Lecture.objects.create(
                course_version=self.version, order=1, title="Duplicate", is_published=True,
            )


class EnrollmentTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.user = User.objects.create_user(email="student@test.com", password="x")
        self.profile = StudentProfile.objects.create(
            user=self.user,
            full_name="Test Student",
            date_of_birth=date(2000, 1, 1),
            gender=GENDER_MALE,
            address="Cairo",
            phone="01000000000",
            national_id="29001010100011",
            education=EDUCATION_BACHELOR,
        )
        self.course = Course.objects.create(title="C1", created_by=self.admin)
        self.version = CourseVersion.objects.create(
            course=self.course, label="v1", access_duration_days=30, created_by=self.admin,
        )
        diploma = Diploma.objects.create(title="D", total_semesters=2, created_by=self.admin)
        self.cohort = Cohort.objects.create(diploma=diploma, name="C1", start_date=date(2026, 1, 1), created_by=self.admin)

    def test_diploma_enrollment_default_pending(self):
        de = DiplomaEnrollment.objects.create(student=self.profile, cohort=self.cohort)
        self.assertEqual(de.status, ENROLLMENT_STATUS_PENDING)

    def test_course_enrollment_default_pending(self):
        ce = CourseEnrollment.objects.create(student=self.profile, course_version=self.version)
        self.assertEqual(ce.status, ENROLLMENT_STATUS_PENDING)

    def test_course_enrollment_is_access_expired_no_expiry(self):
        ce = CourseEnrollment.objects.create(student=self.profile, course_version=self.version)
        self.assertFalse(ce.is_access_expired)

    def test_course_enrollment_is_access_expired(self):
        ce = CourseEnrollment.objects.create(
            student=self.profile,
            course_version=self.version,
            status=ENROLLMENT_STATUS_ACTIVE,
            expires_at=timezone.now() - timedelta(days=1),
        )
        self.assertTrue(ce.is_access_expired)

    def test_course_enrollment_not_expired(self):
        ce = CourseEnrollment.objects.create(
            student=self.profile,
            course_version=self.version,
            status=ENROLLMENT_STATUS_ACTIVE,
            expires_at=timezone.now() + timedelta(days=1),
        )
        self.assertFalse(ce.is_access_expired)


class StaffAssignmentTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin2@test.com", password="x")
        self.teacher = User.objects.create_user(email="teacher@test.com", password="x")
        self.moderator = User.objects.create_user(email="moderator@test.com", password="x")
        self.student_user = User.objects.create_user(email="assigned-student@test.com", password="x")
        self.profile = StudentProfile.objects.create(
            user=self.student_user,
            full_name="Assigned Student",
            date_of_birth=date(2000, 1, 1),
            gender=GENDER_MALE,
            address="Cairo",
            phone="01000000100",
            national_id="29001010100100",
            education=EDUCATION_BACHELOR,
        )
        diploma = Diploma.objects.create(title="Assignment Diploma", total_semesters=2, created_by=self.admin)
        self.cohort = Cohort.objects.create(
            diploma=diploma,
            name="Assignments 2026",
            start_date=date(2026, 1, 1),
            created_by=self.admin,
        )

    def test_teacher_active_assignment_is_unique(self):
        assign_teacher_to_cohort(self.teacher, self.cohort, self.admin)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TeacherAssignment.objects.create(
                    teacher=self.teacher,
                    scope_type=SCOPE_DIPLOMA_COHORT,
                    cohort=self.cohort,
                    assigned_by=self.admin,
                )

    def test_moderator_assignment_merge_does_not_delete_existing_row(self):
        first = assign_moderator_to_students(self.moderator, [self.profile], self.cohort, self.admin)[0]
        second = assign_moderator_to_students(self.moderator, [self.profile], self.cohort, self.admin)[0]

        self.assertEqual(first.id, second.id)
        self.assertEqual(
            ModeratorStudentAssignment.objects.filter(
                moderator=self.moderator,
                student=self.profile,
                cohort=self.cohort,
                active=True,
            ).count(),
            1,
        )

    def test_moderator_assignment_revoke_preserves_history(self):
        assignment = assign_moderator_to_students(self.moderator, [self.profile], self.cohort, self.admin)[0]
        revoke_moderator_student_assignment(assignment, self.admin, "rotated")
        assignment.refresh_from_db()

        self.assertFalse(assignment.active)
        self.assertEqual(assignment.revoke_reason, "rotated")
        self.assertIsNotNone(assignment.revoked_at)


class DiplomaLectureTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.diploma = Diploma.objects.create(title="D", total_semesters=2, created_by=self.admin)
        self.cohort = Cohort.objects.create(
            diploma=self.diploma, name="C1", start_date=date(2026, 1, 1), created_by=self.admin,
        )
        self.semester = Semester.objects.create(diploma=self.diploma, order=1, title="S1")

    def test_diploma_lecture_creation(self):
        dl = DiplomaLecture.objects.create(
            cohort=self.cohort, semester=self.semester, order=1, title="L1", is_published=True,
        )
        self.assertEqual(dl.cohort, self.cohort)
        self.assertEqual(dl.semester, self.semester)
        self.assertEqual(dl.order, 1)
        self.assertEqual(dl.title, "L1")
        self.assertTrue(dl.is_published)

    def test_diploma_lecture_str(self):
        dl = DiplomaLecture.objects.create(
            cohort=self.cohort, semester=self.semester, order=1, title="L1",
        )
        self.assertIn(str(self.cohort), str(dl))
        self.assertIn(str(self.semester), str(dl))
        self.assertIn("1. L1", str(dl))

    def test_diploma_lecture_clean_matching_diploma(self):
        dl = DiplomaLecture(
            cohort=self.cohort, semester=self.semester, order=1, title="L1",
        )
        dl.clean()

    def test_diploma_lecture_clean_mismatched_diploma(self):
        d2 = Diploma.objects.create(title="D2", total_semesters=1, created_by=self.admin)
        s2 = Semester.objects.create(diploma=d2, order=1, title="S2")
        dl = DiplomaLecture(
            cohort=self.cohort, semester=s2, order=1, title="L1",
        )
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            dl.clean()

    def test_diploma_lecture_unique_order(self):
        DiplomaLecture.objects.create(
            cohort=self.cohort, semester=self.semester, order=1, title="L1",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                DiplomaLecture.objects.create(
                    cohort=self.cohort, semester=self.semester, order=1, title="L2",
                )

    def test_diploma_lecture_is_released_published_no_release_at(self):
        dl = DiplomaLecture(
            cohort=self.cohort, semester=self.semester, order=1, title="L1", is_published=True,
        )
        self.assertTrue(dl.is_released)

    def test_diploma_lecture_is_released_not_published(self):
        dl = DiplomaLecture(
            cohort=self.cohort, semester=self.semester, order=1, title="L1", is_published=False,
        )
        self.assertFalse(dl.is_released)

    def test_diploma_lecture_is_released_future_release_at(self):
        dl = DiplomaLecture(
            cohort=self.cohort, semester=self.semester, order=1, title="L1",
            is_published=True, release_at=timezone.now() + timedelta(days=1),
        )
        self.assertFalse(dl.is_released)

    def test_diploma_lecture_is_released_past_release_at(self):
        dl = DiplomaLecture(
            cohort=self.cohort, semester=self.semester, order=1, title="L1",
            is_published=True, release_at=timezone.now() - timedelta(hours=1),
        )
        self.assertTrue(dl.is_released)

    def test_diploma_lecture_youtube_id_extraction(self):
        dl = DiplomaLecture.objects.create(
            cohort=self.cohort, semester=self.semester, order=1, title="L1",
            youtube_url="https://www.youtube.com/watch?v=abc123def45",
        )
        dl.refresh_from_db()
        self.assertEqual(dl.youtube_video_id, "abc123def45")


class DiplomaLectureWatchProgressTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.user = User.objects.create_user(email="s@test.com", password="x")
        self.profile = StudentProfile.objects.create(
            user=self.user, full_name="S", date_of_birth=date(2000, 1, 1),
            gender=GENDER_MALE, address="C", phone="01000000001", national_id="29001010100001",
            education=EDUCATION_BACHELOR,
        )
        self.diploma = Diploma.objects.create(title="D", total_semesters=2, created_by=self.admin)
        self.cohort = Cohort.objects.create(
            diploma=self.diploma, name="C1", start_date=date(2026, 1, 1), created_by=self.admin,
        )
        self.semester = Semester.objects.create(diploma=self.diploma, order=1, title="S1")
        self.diploma_lecture = DiplomaLecture.objects.create(
            cohort=self.cohort, semester=self.semester, order=1, title="L1",
        )
        self.de = DiplomaEnrollment.objects.create(student=self.profile, cohort=self.cohort, status=ENROLLMENT_STATUS_ACTIVE)

    def test_diploma_lecture_watch_progress_creation(self):
        lwp = DiplomaLectureWatchProgress.objects.create(
            student=self.profile, lecture=self.diploma_lecture,
            diploma_enrollment=self.de,
        )
        self.assertEqual(lwp.student, self.profile)
        self.assertEqual(lwp.lecture, self.diploma_lecture)
        self.assertEqual(lwp.diploma_enrollment, self.de)

    def test_diploma_lecture_watch_progress_unique_student_lecture_enrollment(self):
        DiplomaLectureWatchProgress.objects.create(
            student=self.profile, lecture=self.diploma_lecture,
            diploma_enrollment=self.de,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                DiplomaLectureWatchProgress.objects.create(
                    student=self.profile, lecture=self.diploma_lecture,
                    diploma_enrollment=self.de,
                )

    def test_diploma_lecture_watch_progress_validated_non_negative(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                DiplomaLectureWatchProgress.objects.create(
                    student=self.profile, lecture=self.diploma_lecture,
                    diploma_enrollment=self.de,
                    validated_contiguous_seconds=-1,
                )
