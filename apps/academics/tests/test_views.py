"""Tests for the academics app views."""

from datetime import date

from django.contrib.auth.models import Group
from django.test import TestCase
from django.utils import translation
from django.urls import reverse

from apps.academics.models import (
    Cohort,
    Course,
    CourseVersion,
    Diploma,
    Lecture,
    Semester,
)
from apps.academics.services import activate_course_enrollment, enroll_student_in_course
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
            course=cls.course, cohort=cls.cohort, label="v1", created_by=cls.admin,
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
        })
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Course.objects.filter(title="New Course").exists())

    def test_course_edit_get(self):
        r = self.client.get(reverse("academics:admin_course_edit", args=[self.course.id]))
        self.assertEqual(r.status_code, 200)

    def test_attached_course_form_hides_standalone(self):
        diploma = Diploma.objects.create(title="D2", total_semesters=2, created_by=self.admin)
        semester = Semester.objects.create(diploma=diploma, order=1, title="S1")
        course = Course.objects.create(title="Attached", created_by=self.admin)
        from apps.academics.models import SemesterCourse
        SemesterCourse.objects.create(course=course, semester=semester, order=1)
        r = self.client.get(reverse("academics:admin_course_edit", args=[course.id]))
        self.assertNotContains(r, 'name="is_standalone"')

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
        r = self.client.post(reverse("academics:admin_version_create", args=[self.course.id]), {
            "label": "v2",
            "cohort": self.cohort.id,
        })
        self.assertEqual(r.status_code, 302)
        self.assertTrue(CourseVersion.objects.filter(label="v2").exists())

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
        self.assertNotContains(r, "C1")

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
