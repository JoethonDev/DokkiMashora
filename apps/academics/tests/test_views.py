"""Tests for the academics app views."""

from datetime import date

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from apps.academics.constants import ENROLLMENT_STATUS_EXPIRED
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
    enroll_student_in_course,
    enroll_student_in_diploma,
)
from apps.accounts.constants import GROUP_STUDENT
from apps.accounts.models import User
from apps.students.constants import EDUCATION_BACHELOR, GENDER_MALE
from apps.students.models import StudentProfile


def _create_student_profile(user):
    return StudentProfile.objects.create(
        user=user,
        full_name="Test Student",
        date_of_birth=date(2000, 1, 1),
        gender=GENDER_MALE,
        address="Cairo",
        phone="01000000000",
        national_id="29001010100011",
        education=EDUCATION_BACHELOR,
    )


class CourseDetailModalIdTests(TestCase):
    """Course detail renders unique modal IDs for each version and lecture."""

    def setUp(self):
        self.admin = User.objects.create_superuser(email="modal@test.com", password="pass")
        self.client.force_login(self.admin)
        self.course = Course.objects.create(title="Modal Course", created_by=self.admin)
        diploma = Diploma.objects.create(title="Modal D", total_semesters=2, created_by=self.admin)
        self.cohort = Cohort.objects.create(
            diploma=diploma,
            name="Modal C", start_date=date(2026, 1, 1), created_by=self.admin,
        )
        self.cohort2 = Cohort.objects.create(
            diploma=diploma,
            name="Modal C2", start_date=date(2026, 1, 1), created_by=self.admin,
        )
        v1 = CourseVersion.objects.create(course=self.course, cohort=self.cohort, label="", created_by=self.admin)
        v2 = CourseVersion.objects.create(course=self.course, cohort=self.cohort2, label="", created_by=self.admin)
        self.lec1 = Lecture.objects.create(course_version=v1, order=1, title="L1")
        self.lec2 = Lecture.objects.create(course_version=v1, order=2, title="L2")

    def test_version_modal_ids_are_unique(self) -> None:
        response = self.client.get(reverse("academics:admin_course_detail", args=[self.course.id]))
        html = response.content.decode()

        # ponytail: count unique id="del-version-..." patterns
        import re
        version_ids = re.findall(r'id="del-version-(\d+)"', html)
        unique_ids = set(version_ids)
        self.assertEqual(len(version_ids), len(unique_ids),
                         f"Duplicate version modal IDs: {version_ids}")

    def test_lecture_modal_ids_are_unique(self) -> None:
        response = self.client.get(reverse("academics:admin_course_detail", args=[self.course.id]))
        html = response.content.decode()

        import re
        lecture_ids = re.findall(r'id="del-lecture-(\d+)"', html)
        unique_ids = set(lecture_ids)
        self.assertEqual(len(lecture_ids), len(unique_ids),
                         f"Duplicate lecture modal IDs: {lecture_ids}")


class AdminViewTests(TestCase):
    """Test that admin CRUD views render (staff role required)."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(email="admin@test.com", password="pass")
        cls.diploma = Diploma.objects.create(title="D", total_semesters=2, created_by=cls.admin)
        cls.semester = Semester.objects.create(diploma=cls.diploma, order=1, title="S1")
        cls.course = Course.objects.create(title="C1", created_by=cls.admin)
        cls.cohort = Cohort.objects.create(
            diploma=cls.diploma, name="C1", start_date=date(2026, 1, 1), created_by=cls.admin,
        )
        cls.version = CourseVersion.objects.create(
            course=cls.course, cohort=cls.cohort, label="", created_by=cls.admin,
        )
        cls.lecture = Lecture.objects.create(
            course_version=cls.version, order=1, title="L1", is_published=True,
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_diploma_list(self):
        r = self.client.get(reverse("academics:admin_diploma_list"))
        self.assertEqual(r.status_code, 200)

    def test_diploma_detail(self):
        r = self.client.get(reverse("academics:admin_diploma_detail", args=[self.diploma.id]))
        self.assertEqual(r.status_code, 200)

    def test_diploma_create_get(self):
        r = self.client.get(reverse("academics:admin_diploma_create"))
        self.assertEqual(r.status_code, 200)

    def test_diploma_create_post(self):
        r = self.client.post(reverse("academics:admin_diploma_create"), {
            "title": "New Diploma",
            "total_semesters": 3,
            "video_tracking_enabled": False,
            "video_completion_percent": 80,
        })
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Diploma.objects.filter(title="New Diploma").exists())

    def test_diploma_edit_get(self):
        r = self.client.get(reverse("academics:admin_diploma_edit", args=[self.diploma.id]))
        self.assertEqual(r.status_code, 200)

    def test_semester_create_get(self):
        r = self.client.get(reverse("academics:admin_semester_create", args=[self.diploma.id]))
        self.assertEqual(r.status_code, 200)

    def test_semester_create_post(self):
        r = self.client.post(reverse("academics:admin_semester_create", args=[self.diploma.id]), {
            "order": 2,
            "title": "S2",
        })
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Semester.objects.filter(title="S2").exists())

    def test_semester_edit_get(self):
        r = self.client.get(
            reverse("academics:admin_semester_edit", args=[self.diploma.id, self.semester.id]),
        )
        self.assertEqual(r.status_code, 200)

    def test_course_list(self):
        r = self.client.get(reverse("academics:admin_course_list"))
        self.assertEqual(r.status_code, 200)

    def test_course_detail(self):
        r = self.client.get(reverse("academics:admin_course_detail", args=[self.course.id]))
        self.assertEqual(r.status_code, 200)

    def test_course_create_get(self):
        r = self.client.get(reverse("academics:admin_course_create"))
        self.assertEqual(r.status_code, 200)

    def test_course_create_post(self):
        r = self.client.post(reverse("academics:admin_course_create"), {
            "title": "New Course",
            "video_tracking_enabled": False,
            "video_completion_percent": 80,
        })
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Course.objects.filter(title="New Course").exists())

    def test_course_edit_get(self):
        r = self.client.get(reverse("academics:admin_course_edit", args=[self.course.id]))
        self.assertEqual(r.status_code, 200)

    def test_standalone_course_form_still_shows_standalone(self):
        standalone = Course.objects.create(title="Standalone", created_by=self.admin, is_standalone=True)
        r = self.client.get(reverse("academics:admin_course_edit", args=[standalone.id]))
        self.assertContains(r, 'name="is_standalone"')

    def test_cohort_create_get(self):
        r = self.client.get(reverse("academics:admin_cohort_create", args=[self.diploma.id]))
        self.assertEqual(r.status_code, 200)

    def test_cohort_create_post(self):
        r = self.client.post(reverse("academics:admin_cohort_create", args=[self.diploma.id]), {
            "name": "C2",
            "start_date": "2026-06-01",
        })
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Cohort.objects.filter(name="C2").exists())

    def test_cohort_edit_get(self):
        r = self.client.get(
            reverse("academics:admin_cohort_edit", args=[self.diploma.id, self.cohort.id]),
        )
        self.assertEqual(r.status_code, 200)

    def test_version_create_get(self):
        r = self.client.get(reverse("academics:admin_version_create", args=[self.course.id]))
        self.assertEqual(r.status_code, 200)

    def test_version_create_post(self):
        semester = Semester.objects.create(diploma=self.diploma, order=2, title="S2")
        cohort2 = Cohort.objects.create(
            diploma=self.diploma, name="C2", start_date=date(2026, 6, 1), created_by=self.admin,
        )
        r = self.client.post(reverse("academics:admin_version_create", args=[self.course.id]), {
            "label": "",
            "cohort": cohort2.id,
            "semester": semester.id,
        })
        self.assertEqual(r.status_code, 302)
        self.assertTrue(CourseVersion.objects.filter(cohort=cohort2).exists())

    def test_version_detail(self):
        r = self.client.get(
            reverse("academics:admin_version_detail", args=[self.course.id, self.version.id]),
        )
        self.assertEqual(r.status_code, 200)

    def test_lecture_create_get(self):
        r = self.client.get(
            reverse("academics:admin_lecture_create", args=[self.course.id, self.version.id]),
        )
        self.assertEqual(r.status_code, 200)

    def test_lecture_create_post(self):
        r = self.client.post(
            reverse("academics:admin_lecture_create", args=[self.course.id, self.version.id]),
            {"order": 2, "title": "L2", "is_published": True},
        )
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Lecture.objects.filter(title="L2").exists())

    def test_lecture_edit_get(self):
        r = self.client.get(
            reverse(
                "academics:admin_lecture_edit",
                args=[self.course.id, self.version.id, self.lecture.id],
            ),
        )
        self.assertEqual(r.status_code, 200)

    def test_lecture_delete_post(self):
        r = self.client.post(
            reverse(
                "academics:admin_lecture_delete",
                args=[self.course.id, self.version.id, self.lecture.id],
            ),
        )
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Lecture.objects.filter(id=self.lecture.id).exists())

    def test_lecture_delete_get_fails(self):
        r = self.client.get(
            reverse(
                "academics:admin_lecture_delete",
                args=[self.course.id, self.version.id, self.lecture.id],
            ),
        )
        # GET returns 405 (POST required)
        self.assertEqual(r.status_code, 405)


class AdminAuthTests(TestCase):
    """Test that non-staff users cannot access admin views."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(email="admin@test.com", password="pass")
        cls.diploma = Diploma.objects.create(title="D", total_semesters=2, created_by=cls.admin)

    def setUp(self):
        self.student = User.objects.create_user(email="stud@test.com", password="pass")
        self.client.force_login(self.student)

    def test_diploma_list_requires_staff(self):
        r = self.client.get(reverse("academics:admin_diploma_list"))
        self.assertEqual(r.status_code, 403)


class StudentCourseViewTests(TestCase):
    """Test the student course list, detail, and lecture views."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(email="admin@test.com", password="pass")
        cls.course = Course.objects.create(title="C1", created_by=cls.admin)
        cls.version = CourseVersion.objects.create(
            course=cls.course, label="v1", access_duration_days=30, created_by=cls.admin,
        )
        cls.lecture = Lecture.objects.create(
            course_version=cls.version, order=1, title="L1", is_published=True,
        )

    def setUp(self):
        self.user = User.objects.create_user(email="stud@test.com", password="pass")
        self.user.groups.add(Group.objects.get(name=GROUP_STUDENT))
        self.profile = _create_student_profile(self.user)
        self.client.force_login(self.user)

    def test_student_course_list_empty(self):
        r = self.client.get(reverse("academics:student_course_list"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(list(r.context["course_versions"]), [])

    def test_student_course_list_with_enrollment(self):
        ce = enroll_student_in_course(self.profile, self.version)
        activate_course_enrollment(ce)
        r = self.client.get(reverse("academics:student_course_list"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "C1")

    def test_student_course_detail_no_access(self):
        r = self.client.get(reverse("academics:student_course_detail", args=[self.version.id]))
        self.assertEqual(r.status_code, 403)

    def test_student_course_detail_with_access(self):
        ce = enroll_student_in_course(self.profile, self.version)
        activate_course_enrollment(ce)
        r = self.client.get(reverse("academics:student_course_detail", args=[self.version.id]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "L1")

    def test_student_lecture_view_no_access(self):
        r = self.client.get(
            reverse("academics:student_lecture_view", args=[self.version.id, self.lecture.id]),
        )
        self.assertEqual(r.status_code, 403)

    def test_student_lecture_view_with_access(self):
        ce = enroll_student_in_course(self.profile, self.version)
        activate_course_enrollment(ce)
        r = self.client.get(
            reverse("academics:student_lecture_view", args=[self.version.id, self.lecture.id]),
        )
        self.assertEqual(r.status_code, 200)

    def test_student_lecture_view_not_published(self):
        ce = enroll_student_in_course(self.profile, self.version)
        activate_course_enrollment(ce)
        self.lecture.is_published = False
        self.lecture.save(update_fields=["is_published"])
        r = self.client.get(
            reverse("academics:student_lecture_view", args=[self.version.id, self.lecture.id]),
        )
        self.assertEqual(r.status_code, 403)


class StudentAuthTests(TestCase):
    """Test non-student users get 403 on student views."""

    def setUp(self):
        self.user = User.objects.create_user(email="staff@test.com", password="pass")
        self.client.force_login(self.user)

    def test_student_course_list_non_student(self):
        r = self.client.get(reverse("academics:student_course_list"))
        self.assertEqual(r.status_code, 403)

    def test_student_course_detail_non_student(self):
        r = self.client.get(reverse("academics:student_course_detail", args=[1]))
        self.assertEqual(r.status_code, 403)

    def test_student_lecture_view_non_student(self):
        r = self.client.get(reverse("academics:student_lecture_view", args=[1, 1]))
        self.assertEqual(r.status_code, 403)


class StudentNoProfileTests(TestCase):
    """Test student views when user has no StudentProfile."""

    def setUp(self):
        self.user = User.objects.create_user(email="stud@test.com", password="pass")
        self.user.groups.add(Group.objects.get(name=GROUP_STUDENT))
        self.client.force_login(self.user)

    def test_course_list_returns_empty(self):
        with translation.override("en"):
            r = self.client.get(reverse("academics:student_course_list"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "No courses yet")


class DiplomaAdminLectureTests(TestCase):
    """Test admin CRUD views for diploma lectures."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(email="admin@test.com", password="pass")
        cls.diploma = Diploma.objects.create(
            title="D", total_semesters=2, created_by=cls.admin,
        )
        cls.semester = Semester.objects.create(
            diploma=cls.diploma, order=1, title="S1",
        )
        cls.cohort = Cohort.objects.create(
            diploma=cls.diploma, name="C1", start_date=date(2026, 1, 1),
            created_by=cls.admin,
        )
        cls.lecture = DiplomaLecture.objects.create(
            cohort=cls.cohort, semester=cls.semester, order=1,
            title="DL1", is_published=True,
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_admin_diploma_lecture_create_get(self):
        r = self.client.get(
            reverse("academics:admin_diploma_lecture_create",
                    args=[self.diploma.id, self.cohort.id, self.semester.id]),
        )
        self.assertEqual(r.status_code, 200)

    def test_admin_diploma_lecture_create_post(self):
        r = self.client.post(
            reverse("academics:admin_diploma_lecture_create",
                    args=[self.diploma.id, self.cohort.id, self.semester.id]),
            {"order": 2, "title": "DL2", "is_published": True},
        )
        self.assertEqual(r.status_code, 302)
        self.assertTrue(DiplomaLecture.objects.filter(title="DL2").exists())

    def test_admin_diploma_lecture_edit_get(self):
        r = self.client.get(
            reverse("academics:admin_diploma_lecture_edit",
                    args=[self.diploma.id, self.cohort.id, self.semester.id, self.lecture.id]),
        )
        self.assertEqual(r.status_code, 200)

    def test_admin_diploma_lecture_edit_post(self):
        r = self.client.post(
            reverse("academics:admin_diploma_lecture_edit",
                    args=[self.diploma.id, self.cohort.id, self.semester.id, self.lecture.id]),
            {"order": 1, "title": "Updated DL1", "is_published": True},
        )
        self.assertEqual(r.status_code, 302)
        self.lecture.refresh_from_db()
        self.assertEqual(self.lecture.title, "Updated DL1")

    def test_admin_diploma_lecture_delete_post(self):
        r = self.client.post(
            reverse("academics:admin_diploma_lecture_delete",
                    args=[self.diploma.id, self.cohort.id, self.semester.id, self.lecture.id]),
        )
        self.assertEqual(r.status_code, 302)
        self.assertFalse(DiplomaLecture.objects.filter(id=self.lecture.id).exists())

    def test_admin_diploma_lecture_preview(self):
        r = self.client.get(
            reverse("academics:admin_diploma_lecture_preview",
                    args=[self.diploma.id, self.cohort.id, self.semester.id, self.lecture.id]),
        )
        self.assertEqual(r.status_code, 200)

    def test_admin_diploma_lecture_create_forbidden_for_student(self):
        self.client.logout()
        student_user = User.objects.create_user(email="stud@test.com", password="pass")
        student_user.groups.add(Group.objects.get(name=GROUP_STUDENT))
        self.client.force_login(student_user)
        r = self.client.get(
            reverse("academics:admin_diploma_lecture_create",
                    args=[self.diploma.id, self.cohort.id, self.semester.id]),
        )
        self.assertEqual(r.status_code, 403)


class DiplomaStudentViewTests(TestCase):
    """Test student diploma content views and standalone regression."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(email="admin@test.com", password="pass")
        cls.diploma = Diploma.objects.create(
            title="D", total_semesters=2, created_by=cls.admin,
        )
        cls.semester = Semester.objects.create(
            diploma=cls.diploma, order=1, title="S1",
        )
        cls.cohort = Cohort.objects.create(
            diploma=cls.diploma, name="C1", start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31), created_by=cls.admin,
        )
        cls.lecture = DiplomaLecture.objects.create(
            cohort=cls.cohort, semester=cls.semester, order=1,
            title="DL1", is_published=True,
        )
        cls.course = Course.objects.create(title="Standalone", created_by=cls.admin)
        cls.version = CourseVersion.objects.create(
            course=cls.course, label="v1", access_duration_days=30,
            created_by=cls.admin,
        )
        cls.standalone_lecture = Lecture.objects.create(
            course_version=cls.version, order=1, title="SL1", is_published=True,
        )

    def setUp(self):
        self.user = User.objects.create_user(email="stud@test.com", password="pass")
        self.user.groups.add(Group.objects.get(name=GROUP_STUDENT))
        self.profile = _create_student_profile(self.user)
        self.client.force_login(self.user)

    def _enroll_active(self):
        de = enroll_student_in_diploma(self.profile, self.cohort)
        activate_diploma_enrollment(de)
        return de

    def test_student_diploma_detail_renders(self):
        de = self._enroll_active()
        r = self.client.get(
            reverse("academics:student_diploma_detail", args=[de.id]),
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "DL1")

    def test_student_diploma_detail_no_enrollment(self):
        r = self.client.get(
            reverse("academics:student_diploma_detail", args=[999]),
        )
        self.assertEqual(r.status_code, 404)

    def test_student_diploma_detail_expired_enrollment(self):
        de = self._enroll_active()
        de.status = ENROLLMENT_STATUS_EXPIRED
        de.save(update_fields=["status"])
        r = self.client.get(
            reverse("academics:student_diploma_detail", args=[de.id]),
        )
        self.assertEqual(r.status_code, 403)

    def test_student_diploma_lecture_view(self):
        de = self._enroll_active()
        r = self.client.get(
            reverse("academics:student_diploma_lecture_view",
                    args=[de.id, self.semester.id, self.lecture.id]),
        )
        self.assertEqual(r.status_code, 200)

    def test_student_diploma_lecture_view_not_published(self):
        de = self._enroll_active()
        self.lecture.is_published = False
        self.lecture.save(update_fields=["is_published"])
        r = self.client.get(
            reverse("academics:student_diploma_lecture_view",
                    args=[de.id, self.semester.id, self.lecture.id]),
        )
        self.assertEqual(r.status_code, 403)

    def test_student_diploma_lecture_view_wrong_cohort(self):
        de = self._enroll_active()
        other_cohort = Cohort.objects.create(
            diploma=self.diploma, name="C2", start_date=date(2027, 1, 1),
            created_by=self.admin,
        )
        other_lecture = DiplomaLecture.objects.create(
            cohort=other_cohort, semester=self.semester, order=1,
            title="Other", is_published=True,
        )
        r = self.client.get(
            reverse("academics:student_diploma_lecture_view",
                    args=[de.id, self.semester.id, other_lecture.id]),
        )
        self.assertEqual(r.status_code, 404)

    def test_student_diploma_lecture_audio_access(self):
        from unittest.mock import patch
        de = self._enroll_active()
        with patch("apps.academics.views.storage_service.download_file", return_value=b"audio"):
            r = self.client.get(
                reverse("academics:student_diploma_lecture_audio",
                        args=[de.id, self.semester.id, self.lecture.id, "file.mp3"]),
            )
        self.assertEqual(r.status_code, 200)

    def test_standalone_course_detail_still_works(self):
        ce = enroll_student_in_course(self.profile, self.version)
        activate_course_enrollment(ce)
        r = self.client.get(
            reverse("academics:student_course_detail", args=[self.version.id]),
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "SL1")

    def test_standalone_lecture_view_still_works(self):
        ce = enroll_student_in_course(self.profile, self.version)
        activate_course_enrollment(ce)
        r = self.client.get(
            reverse("academics:student_lecture_view",
                    args=[self.version.id, self.standalone_lecture.id]),
        )
        self.assertEqual(r.status_code, 200)

    def test_student_course_list_shows_diploma_section(self):
        self._enroll_active()
        ce = enroll_student_in_course(self.profile, self.version)
        activate_course_enrollment(ce)
        r = self.client.get(reverse("academics:student_course_list"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Standalone")
        self.assertContains(r, self.diploma.title)

    def test_watch_api_start_expired_enrollment_returns_403(self):
        self.diploma.video_tracking_enabled = True
        self.diploma.save(update_fields=["video_tracking_enabled"])
        de = self._enroll_active()
        de.status = ENROLLMENT_STATUS_EXPIRED
        de.save(update_fields=["status"])
        r = self.client.post(
            reverse("academics:api_diploma_watch_start",
                    args=[de.id, self.semester.id, self.lecture.id]),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)
        self.assertIn("enrollment_not_active", r.json().get("error", ""))

    def test_watch_api_checkpoint_expired_enrollment_returns_403(self):
        de = self._enroll_active()
        de.status = ENROLLMENT_STATUS_EXPIRED
        de.save(update_fields=["status"])
        r = self.client.post(
            reverse("academics:api_diploma_watch_checkpoint",
                    args=[de.id, self.semester.id, self.lecture.id]),
            content_type="application/json",
            data='{"session_id":"00000000-0000-0000-0000-000000000000","sequence":1,"position":10,"player_state":"playing","rate":1}',
        )
        self.assertEqual(r.status_code, 403)
        self.assertIn("enrollment_not_active", r.json().get("error", ""))

    def test_watch_api_start_unreleased_lecture_returns_403(self):
        self.diploma.video_tracking_enabled = True
        self.diploma.save(update_fields=["video_tracking_enabled"])
        self.lecture.is_published = False
        self.lecture.save(update_fields=["is_published"])
        de = self._enroll_active()
        r = self.client.post(
            reverse("academics:api_diploma_watch_start",
                    args=[de.id, self.semester.id, self.lecture.id]),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)
        self.assertIn("lecture_not_available", r.json().get("error", ""))
