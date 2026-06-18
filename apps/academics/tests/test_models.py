"""Tests for the academics app models."""

from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from apps.academics.constants import (
    ENROLLMENT_STATUS_ACTIVE,
    ENROLLMENT_STATUS_PENDING,
)
from apps.academics.models import (
    Cohort,
    Course,
    CourseEnrollment,
    CourseVersion,
    Diploma,
    DiplomaEnrollment,
    Lecture,
    Semester,
    SemesterCourse,
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

    def test_default_not_standalone(self):
        self.assertFalse(self.course.is_standalone)


class SemesterCourseTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.diploma = Diploma.objects.create(title="D", total_semesters=2, created_by=self.admin)
        self.semester = Semester.objects.create(diploma=self.diploma, order=1, title="S1")
        self.course = Course.objects.create(title="C1", created_by=self.admin)
        self.sc = SemesterCourse.objects.create(semester=self.semester, course=self.course, order=1)

    def test_str(self):
        self.assertIn(self.semester.title, str(self.sc))
        self.assertIn(self.course.title, str(self.sc))


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
            course=self.course, cohort=cohort, label="v1", created_by=self.admin,
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
