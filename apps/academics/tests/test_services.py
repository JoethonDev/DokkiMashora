"""Tests for the academics app services."""

from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from apps.academics.constants import (
    ENROLLMENT_STATUS_ACTIVE,
    ENROLLMENT_STATUS_EXPIRED,
    ENROLLMENT_STATUS_PENDING,
)
from apps.academics.models import (
    Cohort,
    Course,
    CourseVersion,
    Diploma,
    Lecture,
)
from apps.academics.services import (
    activate_course_enrollment,
    activate_diploma_enrollment,
    can_access_course,
    can_access_lecture,
    enroll_student_in_course,
    enroll_student_in_diploma,
    expire_stale_enrollments,
    get_student_course_versions,
    get_visible_lectures,
)
from apps.accounts.models import User
from apps.students.constants import EDUCATION_BACHELOR, GENDER_MALE
from apps.students.models import StudentProfile


class EnrollmentServiceTests(TestCase):
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
        self.cohort = Cohort.objects.create(
            diploma=diploma, name="C1", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31), created_by=self.admin,
        )

    def test_enroll_student_in_course_creates_pending(self):
        ce = enroll_student_in_course(self.profile, self.version)
        self.assertEqual(ce.status, ENROLLMENT_STATUS_PENDING)
        self.assertEqual(ce.student, self.profile)

    def test_enroll_student_in_course_idempotent(self):
        ce1 = enroll_student_in_course(self.profile, self.version)
        ce2 = enroll_student_in_course(self.profile, self.version)
        self.assertEqual(ce1.id, ce2.id)

    def test_enroll_student_in_diploma_creates_pending(self):
        de = enroll_student_in_diploma(self.profile, self.cohort)
        self.assertEqual(de.status, ENROLLMENT_STATUS_PENDING)

    def test_activate_course_enrollment_sets_active(self):
        ce = enroll_student_in_course(self.profile, self.version)
        activate_course_enrollment(ce)
        ce.refresh_from_db()
        self.assertEqual(ce.status, ENROLLMENT_STATUS_ACTIVE)
        self.assertIsNotNone(ce.activated_at)
        self.assertIsNotNone(ce.expires_at)

    def test_activate_course_enrollment_without_duration(self):
        version2 = CourseVersion.objects.create(
            course=self.course, label="v2", access_duration_days=None, created_by=self.admin,
        )
        ce = enroll_student_in_course(self.profile, version2)
        activate_course_enrollment(ce)
        ce.refresh_from_db()
        self.assertIsNone(ce.expires_at)

    def test_activate_diploma_enrollment_sets_expiry_from_cohort(self):
        de = enroll_student_in_diploma(self.profile, self.cohort)
        activate_diploma_enrollment(de)
        de.refresh_from_db()
        self.assertEqual(de.status, ENROLLMENT_STATUS_ACTIVE)
        self.assertIsNotNone(de.expires_at)

    def test_expire_stale_enrollments(self):
        ce = enroll_student_in_course(self.profile, self.version)
        activate_course_enrollment(ce)
        ce.expires_at = timezone.now() - timedelta(days=1)
        ce.save(update_fields=["expires_at"])
        expire_stale_enrollments()
        ce.refresh_from_db()
        self.assertEqual(ce.status, ENROLLMENT_STATUS_EXPIRED)


class AccessPolicyTests(TestCase):
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
        self.ce = enroll_student_in_course(self.profile, self.version)
        activate_course_enrollment(self.ce)
        self.lecture = Lecture.objects.create(
            course_version=self.version, order=1, title="L1", is_published=True,
        )

    def test_can_access_course_true(self):
        self.assertTrue(can_access_course(self.profile, self.version))

    def test_can_access_course_no_enrollment(self):
        version2 = CourseVersion.objects.create(
            course=self.course, label="v2", created_by=self.admin,
        )
        self.assertFalse(can_access_course(self.profile, version2))

    def test_can_access_course_expired(self):
        self.ce.expires_at = timezone.now() - timedelta(days=1)
        self.ce.save(update_fields=["expires_at"])
        self.assertFalse(can_access_course(self.profile, self.version))

    def test_can_access_lecture_true(self):
        self.assertTrue(can_access_lecture(self.profile, self.lecture))

    def test_can_access_lecture_not_released(self):
        self.lecture.is_published = False
        self.assertFalse(can_access_lecture(self.profile, self.lecture))

    def test_can_access_lecture_no_course_access(self):
        self.ce.expires_at = timezone.now() - timedelta(days=1)
        self.ce.save(update_fields=["expires_at"])
        self.assertFalse(can_access_lecture(self.profile, self.lecture))

    def test_get_student_course_versions(self):
        versions = get_student_course_versions(self.profile)
        self.assertIn(self.version, versions)

    def test_get_visible_lectures(self):
        lectures = get_visible_lectures(self.profile, self.version)
        self.assertIn(self.lecture, lectures)

    def test_get_visible_lectures_no_access(self):
        self.ce.expires_at = timezone.now() - timedelta(days=1)
        self.ce.save(update_fields=["expires_at"])
        lectures = get_visible_lectures(self.profile, self.version)
        self.assertEqual(len(lectures), 0)

    def test_get_visible_lectures_future_release(self):
        self.lecture.release_at = timezone.now() + timedelta(days=1)
        self.lecture.save(update_fields=["release_at"])
        lectures = get_visible_lectures(self.profile, self.version)
        self.assertNotIn(self.lecture, lectures)
