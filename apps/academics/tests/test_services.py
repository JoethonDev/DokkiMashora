"""Tests for the academics app services."""

from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from apps.academics.constants import (
    ENROLLMENT_STATUS_ACTIVE,
    ENROLLMENT_STATUS_EXPIRED,
)
from apps.academics.models import (
    Cohort,
    Course,
    CourseVersion,
    Diploma,
    DiplomaLecture,
    Lecture,
    Semester,
)
from apps.academics.services import (
    activate_course_enrollment,
    activate_diploma_enrollment,
    can_access_course,
    can_access_diploma_lecture,
    can_access_lecture,
    create_cohort,
    enroll_student_in_course,
    enroll_student_in_diploma,
    expire_stale_enrollments,
    get_student_course_versions,
    get_visible_diploma_lectures,
    get_visible_lectures,
    record_diploma_lecture_checkpoint,
    start_diploma_lecture_watch,
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

    def test_enroll_student_in_course_creates_active(self):
        ce = enroll_student_in_course(self.profile, self.version)
        self.assertEqual(ce.status, ENROLLMENT_STATUS_ACTIVE)
        self.assertEqual(ce.student, self.profile)

    def test_enroll_student_in_course_idempotent(self):
        ce1 = enroll_student_in_course(self.profile, self.version)
        ce2 = enroll_student_in_course(self.profile, self.version)
        self.assertEqual(ce1.id, ce2.id)

    def test_enroll_student_in_diploma_creates_active(self):
        de = enroll_student_in_diploma(self.profile, self.cohort)
        self.assertEqual(de.status, ENROLLMENT_STATUS_ACTIVE)

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


class YouTubeMetadataTests(TestCase):
    """Tests for YouTube video ID extraction and metadata fetching."""

    def setUp(self):
        from apps.accounts.models import User
        self.user = User.objects.create_user("yt@test.com", password="pass")

    def test_extract_standard_url(self):
        from apps.academics.services import extract_youtube_video_id
        self.assertEqual(
            extract_youtube_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_extract_short_url(self):
        from apps.academics.services import extract_youtube_video_id
        self.assertEqual(
            extract_youtube_video_id("https://youtu.be/dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_extract_embed_url(self):
        from apps.academics.services import extract_youtube_video_id
        self.assertEqual(
            extract_youtube_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_extract_shorts_url(self):
        from apps.academics.services import extract_youtube_video_id
        self.assertEqual(
            extract_youtube_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_extract_non_youtube_url(self):
        from apps.academics.services import extract_youtube_video_id
        self.assertEqual(extract_youtube_video_id("https://vimeo.com/123456"), "")

    def test_extract_empty_string(self):
        from apps.academics.services import extract_youtube_video_id
        self.assertEqual(extract_youtube_video_id(""), "")

    def test_populate_youtube_metadata_extracts_id(self):
        from unittest.mock import patch

        from apps.academics.services import populate_youtube_metadata
        course = Course.objects.create(title="YT", created_by=self.user)
        version = CourseVersion.objects.create(course=course, label="v1", created_by=self.user)
        lecture = Lecture.objects.create(
            course_version=version, order=1, title="L1",
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )
        with patch("apps.academics.services.fetch_youtube_duration_seconds", return_value=None):
            populate_youtube_metadata(lecture=lecture)
        lecture.refresh_from_db()
        self.assertEqual(lecture.youtube_video_id, "dQw4w9WgXcQ")
        self.assertIsNotNone(lecture.youtube_metadata_fetched_at)

    def test_populate_youtube_metadata_clears_id_on_blank_url(self):
        from apps.academics.services import populate_youtube_metadata
        course = Course.objects.create(title="YT2", created_by=self.user)
        version = CourseVersion.objects.create(course=course, label="v1", created_by=self.user)
        lecture = Lecture.objects.create(
            course_version=version, order=1, title="L1",
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            youtube_video_id="dQw4w9WgXcQ",
        )
        lecture.youtube_url = ""
        populate_youtube_metadata(lecture=lecture)
        lecture.refresh_from_db()
        self.assertEqual(lecture.youtube_video_id, "")

    def test_populate_youtube_metadata_with_duration(self):
        from unittest.mock import patch

        from apps.academics.services import populate_youtube_metadata
        course = Course.objects.create(title="YT3", created_by=self.user)
        version = CourseVersion.objects.create(course=course, label="v1", created_by=self.user)
        lecture = Lecture.objects.create(
            course_version=version, order=1, title="L1",
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )
        with patch("apps.academics.services.fetch_youtube_duration_seconds", return_value=213):
            populate_youtube_metadata(lecture=lecture)
        lecture.refresh_from_db()
        self.assertEqual(lecture.youtube_duration_seconds, 213)


class WatchServerTests(TestCase):
    """Tests for watch server start/checkpoint services."""

    def setUp(self):
        from datetime import date

        from apps.academics.services import activate_course_enrollment, enroll_student_in_course
        from apps.accounts.models import User
        from apps.students.models import StudentProfile
        self.user = User.objects.create_user("watch@test.com", password="pass")
        self.student = StudentProfile.objects.create(user=self.user, full_name="W", date_of_birth=date(2000, 1, 1))
        self.course = Course.objects.create(title="WC", created_by=self.user)
        self.version = CourseVersion.objects.create(course=self.course, label="v1", created_by=self.user)
        self.lecture = Lecture.objects.create(
            course_version=self.version, order=1, title="WL1",
            youtube_video_id="abc12345678", youtube_duration_seconds=120,
        )
        self.course.video_tracking_enabled = True
        self.course.save(update_fields=["video_tracking_enabled"])
        self.ce = enroll_student_in_course(self.student, self.version)
        activate_course_enrollment(self.ce)

    def test_start_returns_session(self):
        from apps.academics.services import start_lecture_watch
        result = start_lecture_watch(student=self.student, lecture=self.lecture)
        self.assertIn("session_id", result)
        self.assertEqual(result["duration"], 120)
        self.assertFalse(result["completed"])

    def test_start_no_enrollment(self):
        from datetime import date

        from apps.academics.services import start_lecture_watch
        from apps.accounts.models import User
        from apps.students.models import StudentProfile
        user2 = User.objects.create_user("noenroll@test.com", password="pass")
        sp2 = StudentProfile.objects.create(user=user2, full_name="X", date_of_birth=date(2000, 1, 1), phone="+1000000002", national_id="NID002")
        with self.assertRaises(ValueError):
            start_lecture_watch(student=sp2, lecture=self.lecture)

    def test_checkpoint_advances_progress(self):
        from django.utils import timezone

        from apps.academics.services import record_lecture_checkpoint, start_lecture_watch
        start = start_lecture_watch(student=self.student, lecture=self.lecture)
        sid = start["session_id"]
        now = timezone.now()
        result = record_lecture_checkpoint(
            student=self.student, lecture=self.lecture,
            payload={"session_id": sid, "sequence": 1, "position": 10, "player_state": "playing", "rate": 1},
            now=now,
        )
        self.assertEqual(result["status"], "in_progress")
        self.assertGreater(result["progress"], 0)

    def test_checkpoint_violation_on_speed(self):
        from django.utils import timezone

        from apps.academics.services import record_lecture_checkpoint, start_lecture_watch
        start = start_lecture_watch(student=self.student, lecture=self.lecture)
        sid = start["session_id"]
        now = timezone.now()
        result = record_lecture_checkpoint(
            student=self.student, lecture=self.lecture,
            payload={"session_id": sid, "sequence": 1, "position": 10, "player_state": "playing", "rate": 2},
            now=now,
        )
        self.assertEqual(result["status"], 409)
        self.assertEqual(result["violation"], "speed")

    def test_checkpoint_out_of_order(self):
        from django.utils import timezone

        from apps.academics.services import record_lecture_checkpoint, start_lecture_watch
        start = start_lecture_watch(student=self.student, lecture=self.lecture)
        sid = start["session_id"]
        now = timezone.now()
        # Skip seq 1
        result = record_lecture_checkpoint(
            student=self.student, lecture=self.lecture,
            payload={"session_id": sid, "sequence": 2, "position": 10, "player_state": "playing", "rate": 1},
            now=now,
        )
        self.assertEqual(result["status"], 409)
        self.assertEqual(result["violation"], "invalid_sequence")

    def test_completion_at_threshold(self):
        from datetime import timedelta

        from django.utils import timezone

        from apps.academics.services import record_lecture_checkpoint, start_lecture_watch
        start = start_lecture_watch(student=self.student, lecture=self.lecture)
        sid = start["session_id"]
        now = timezone.now()
        # Simulate watching 100 seconds of 120 = 83.3% > 80%
        for i in range(1, 21):
            t = now + timedelta(seconds=i * 5)
            result = record_lecture_checkpoint(
                student=self.student, lecture=self.lecture,
                payload={"session_id": sid, "sequence": i, "position": i * 5, "player_state": "playing", "rate": 1},
                now=t,
            )
        self.assertEqual(result["status"], "completed")


class DiplomaAccessPolicyTests(TestCase):
    """Tests for diploma-specific access policy services."""

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
        self.diploma = Diploma.objects.create(
            title="D", total_semesters=2, created_by=self.admin,
        )
        self.semester1 = Semester.objects.create(
            diploma=self.diploma, order=1, title="S1",
        )
        self.semester2 = Semester.objects.create(
            diploma=self.diploma, order=2, title="S2",
        )
        self.cohort = Cohort.objects.create(
            diploma=self.diploma, name="C1", start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31), created_by=self.admin,
        )
        self.de = enroll_student_in_diploma(self.profile, self.cohort)
        activate_diploma_enrollment(self.de)
        self.diploma_lecture = DiplomaLecture.objects.create(
            cohort=self.cohort, semester=self.semester1, order=1,
            title="DL1", is_published=True,
        )
        self.course = Course.objects.create(title="Standalone", created_by=self.admin)
        self.version = CourseVersion.objects.create(
            course=self.course, label="v1", access_duration_days=30,
            created_by=self.admin,
        )

    def test_create_cohort_without_versions(self):
        new_cohort = create_cohort(
            diploma=self.diploma,
            form_data={"name": "C2", "start_date": date(2027, 1, 1)},
            actor=self.admin,
        )
        self.assertTrue(Cohort.objects.filter(id=new_cohort.id).exists())

    def test_get_visible_diploma_lectures(self):
        lectures = get_visible_diploma_lectures(self.profile, self.de)
        self.assertIn(self.diploma_lecture, lectures)
        unpublished = DiplomaLecture.objects.create(
            cohort=self.cohort, semester=self.semester1, order=2,
            title="Unpub", is_published=False,
        )
        self.assertNotIn(unpublished, lectures)

    def test_get_visible_diploma_lectures_no_access(self):
        self.de.status = ENROLLMENT_STATUS_EXPIRED
        self.de.save(update_fields=["status"])
        lectures = get_visible_diploma_lectures(self.profile, self.de)
        self.assertEqual(len(lectures), 0)

    def test_get_visible_diploma_lectures_filtered_by_semester(self):
        s2_lecture = DiplomaLecture.objects.create(
            cohort=self.cohort, semester=self.semester2, order=1,
            title="S2L1", is_published=True,
        )
        lectures = get_visible_diploma_lectures(
            self.profile, self.de, semester=self.semester1,
        )
        self.assertIn(self.diploma_lecture, lectures)
        self.assertNotIn(s2_lecture, lectures)

    def test_can_access_diploma_lecture_true(self):
        self.assertTrue(
            can_access_diploma_lecture(self.profile, self.de, self.diploma_lecture),
        )

    def test_can_access_diploma_lecture_wrong_cohort(self):
        other_cohort = Cohort.objects.create(
            diploma=self.diploma, name="C2", start_date=date(2027, 1, 1),
            created_by=self.admin,
        )
        other_lecture = DiplomaLecture.objects.create(
            cohort=other_cohort, semester=self.semester1, order=1,
            title="Other", is_published=True,
        )
        self.assertFalse(
            can_access_diploma_lecture(self.profile, self.de, other_lecture),
        )

    def test_can_access_diploma_lecture_expired(self):
        self.de.status = ENROLLMENT_STATUS_EXPIRED
        self.de.save(update_fields=["status"])
        self.assertFalse(
            can_access_diploma_lecture(self.profile, self.de, self.diploma_lecture),
        )

    def test_can_access_diploma_lecture_not_published(self):
        self.diploma_lecture.is_published = False
        self.diploma_lecture.save(update_fields=["is_published"])
        self.assertFalse(
            can_access_diploma_lecture(self.profile, self.de, self.diploma_lecture),
        )


class DiplomaWatchServerTests(TestCase):
    """Tests for diploma watch server start/checkpoint services."""

    def setUp(self):
        from datetime import date
        self.user = User.objects.create_user(email="watch_diploma@test.com", password="pass")
        self.student = StudentProfile.objects.create(
            user=self.user, full_name="W", date_of_birth=date(2000, 1, 1),
            phone="01000000001", national_id="NID001",
        )
        self.diploma = Diploma.objects.create(
            title="DW", total_semesters=2, video_tracking_enabled=True,
            video_completion_percent=80, created_by=self.user,
        )
        self.semester = Semester.objects.create(
            diploma=self.diploma, order=1, title="S1",
        )
        self.cohort = Cohort.objects.create(
            diploma=self.diploma, name="C1", start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31), created_by=self.user,
        )
        self.de = enroll_student_in_diploma(self.student, self.cohort)
        activate_diploma_enrollment(self.de)
        self.lecture = DiplomaLecture.objects.create(
            cohort=self.cohort, semester=self.semester, order=1, title="DL1",
            youtube_video_id="abc12345678", youtube_duration_seconds=120,
        )

    def test_start_diploma_lecture_watch_tracking_disabled(self):
        self.diploma.video_tracking_enabled = False
        self.diploma.save(update_fields=["video_tracking_enabled"])
        with self.assertRaises(ValueError):
            start_diploma_lecture_watch(student=self.student, enrollment=self.de, lecture=self.lecture)

    def test_start_diploma_lecture_watch_returns_session(self):
        result = start_diploma_lecture_watch(student=self.student, enrollment=self.de, lecture=self.lecture)
        self.assertIn("session_id", result)
        self.assertEqual(result["duration"], 120)
        self.assertFalse(result["completed"])

    def test_start_diploma_lecture_watch_no_enrollment(self):
        from django.db import IntegrityError
        user2 = User.objects.create_user(email="noenroll_dl@test.com", password="pass")
        sp2 = StudentProfile.objects.create(
            user=user2, full_name="X", date_of_birth=date(2000, 1, 1),
            phone="+1000000002", national_id="NID002",
        )
        with self.assertRaises(IntegrityError):
            start_diploma_lecture_watch(student=sp2, enrollment=None, lecture=self.lecture)

    def test_diploma_checkpoint_advances_progress(self):
        from django.utils import timezone
        start = start_diploma_lecture_watch(student=self.student, enrollment=self.de, lecture=self.lecture)
        sid = start["session_id"]
        now = timezone.now()
        result = record_diploma_lecture_checkpoint(
            student=self.student, enrollment=self.de, lecture=self.lecture,
            payload={"session_id": sid, "sequence": 1, "position": 10, "player_state": "playing", "rate": 1},
            now=now,
        )
        self.assertEqual(result["status"], "in_progress")
        self.assertGreater(result["progress"], 0)

    def test_diploma_checkpoint_violation_on_speed(self):
        from django.utils import timezone
        start = start_diploma_lecture_watch(student=self.student, enrollment=self.de, lecture=self.lecture)
        sid = start["session_id"]
        now = timezone.now()
        result = record_diploma_lecture_checkpoint(
            student=self.student, enrollment=self.de, lecture=self.lecture,
            payload={"session_id": sid, "sequence": 1, "position": 10, "player_state": "playing", "rate": 2},
            now=now,
        )
        self.assertEqual(result["status"], 409)
        self.assertEqual(result["violation"], "speed")

    def test_diploma_checkpoint_out_of_order(self):
        from django.utils import timezone
        start = start_diploma_lecture_watch(student=self.student, enrollment=self.de, lecture=self.lecture)
        sid = start["session_id"]
        now = timezone.now()
        result = record_diploma_lecture_checkpoint(
            student=self.student, enrollment=self.de, lecture=self.lecture,
            payload={"session_id": sid, "sequence": 2, "position": 10, "player_state": "playing", "rate": 1},
            now=now,
        )
        self.assertEqual(result["status"], 409)
        self.assertEqual(result["violation"], "invalid_sequence")

    def test_diploma_completion_at_threshold(self):
        from datetime import timedelta

        from django.utils import timezone
        start = start_diploma_lecture_watch(student=self.student, enrollment=self.de, lecture=self.lecture)
        sid = start["session_id"]
        now = timezone.now()
        for i in range(1, 21):
            t = now + timedelta(seconds=i * 5)
            result = record_diploma_lecture_checkpoint(
                student=self.student, enrollment=self.de, lecture=self.lecture,
                payload={"session_id": sid, "sequence": i, "position": i * 5, "player_state": "playing", "rate": 1},
                now=t,
            )
        self.assertEqual(result["status"], "completed")
