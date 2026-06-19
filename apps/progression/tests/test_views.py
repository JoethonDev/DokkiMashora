"""Tests for progression view permissions and template rendering."""
from decimal import Decimal

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import override

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import Cohort, Diploma, DiplomaEnrollment, Semester
from apps.accounts.constants import (
    GROUP_ADMIN,
    GROUP_MODERATOR,
    GROUP_STUDENT,
    GROUP_SUPER_ADMIN,
    GROUP_TEACHER,
)
from apps.accounts.models import User
from apps.assessments.constants import ASSESSMENT_TYPE_QUIZ
from apps.assessments.models import Assessment
from apps.certificates.models import GeneratedCertificate
from apps.progression.constants import (
    CERTIFICATE_STATUS_AVAILABLE,
    CERTIFICATE_STATUS_REVOKED,
    PROMOTION_DECISION_MANUAL_PROMOTE,
    SEMESTER_PROGRESS_IN_PROGRESS,
    SEMESTER_PROGRESS_LOCKED,
)
from apps.progression.models import PromotionDecision, StudentDiplomaCompletion, StudentSemesterProgress
from apps.students.models import StudentProfile


def _create_user(email, group_name):
    user = User.objects.create_user(email=email, password="pass123")
    group, _ = Group.objects.get_or_create(name=group_name)
    user.groups.add(group)
    return user


class ProgressionAdminViewPermissionTests(TestCase):
    """Only SENIOR_STAFF (admin, super_admin) may access admin progression views."""

    def setUp(self):
        self.super_admin = _create_user("super@test.com", GROUP_SUPER_ADMIN)
        self.admin = _create_user("admin@test.com", GROUP_ADMIN)
        self.moderator = _create_user("mod@test.com", GROUP_MODERATOR)
        self.teacher = _create_user("teacher@test.com", GROUP_TEACHER)
        self.student_user = _create_user("student@test.com", GROUP_STUDENT)
        self.student = StudentProfile.objects.create(
            user=self.student_user,
            full_name="Student",
            date_of_birth=timezone.now().date() - timezone.timedelta(days=365 * 20),
            gender="male",
            address="Addr",
            phone="01234567890",
            national_id="12345678901234",
            education="bachelor",
        )
        self.diploma = Diploma.objects.create(title="D", total_semesters=1, created_by=self.admin)
        self.cohort = Cohort.objects.create(
            diploma=self.diploma, name="C1", start_date=timezone.now().date(),
            created_by=self.admin,
        )
        self.enrollment = DiplomaEnrollment.objects.create(
            student=self.student, cohort=self.cohort, status=ENROLLMENT_STATUS_ACTIVE,
        )

    def _assert_role_access(self, url, allowed_users, denied_users):
        for user in allowed_users:
            self.client.force_login(user)
            response = self.client.get(url)
            self.assertIn(response.status_code, (200, 302), f"{user.email} should access {url}")
        for user in denied_users:
            self.client.force_login(user)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403, f"{user.email} should be denied {url}")
        self.client.logout()
        response = self.client.get(url)
        self.assertIn(response.status_code, (302, 403), "Anonymous should be redirected or denied")

    def test_admin_dashboard_allowed(self):
        self._assert_role_access(
            reverse("progression:admin_dashboard"),
            allowed_users=[self.super_admin, self.admin],
            denied_users=[self.moderator, self.teacher, self.student_user],
        )

    def test_admin_student_detail_allowed(self):
        self._assert_role_access(
            reverse("progression:admin_student_detail", args=[self.student.id]),
            allowed_users=[self.super_admin, self.admin],
            denied_users=[self.moderator, self.teacher, self.student_user],
        )


class ProgressionTemplateRenderTests(TestCase):
    """Verify templates render required summary sections."""

    def setUp(self):
        self.admin = _create_user("admin@test.com", GROUP_ADMIN)
        self.student_user = _create_user("student@test.com", GROUP_STUDENT)
        self.student = StudentProfile.objects.create(
            user=self.student_user,
            full_name="Test Student",
            date_of_birth=timezone.now().date() - timezone.timedelta(days=365 * 20),
            gender="male",
            address="Addr",
            phone="01234567890",
            national_id="12345678901234",
            education="bachelor",
        )
        self.diploma = Diploma.objects.create(title="Test Diploma", total_semesters=2, created_by=self.admin)
        self.sem1 = Semester.objects.create(diploma=self.diploma, order=1, title="Semester 1")
        self.sem2 = Semester.objects.create(diploma=self.diploma, order=2, title="Semester 2")
        self.cohort = Cohort.objects.create(
            diploma=self.diploma, name="C1", start_date=timezone.now().date(),
            created_by=self.admin,
        )
        self.enrollment = DiplomaEnrollment.objects.create(
            student=self.student, cohort=self.cohort, status=ENROLLMENT_STATUS_ACTIVE,
        )

    def test_admin_detail_renders_completion_section(self):
        """Admin student detail renders the Diploma Completion section."""
        with override("en"):
            StudentDiplomaCompletion.objects.create(
                student=self.student, diploma_enrollment=self.enrollment,
                status="completed", completed_at=timezone.now(),
            )
            self.client.force_login(self.admin)
            response = self.client.get(reverse("progression:admin_student_detail", args=[self.student.id]))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Diploma Completion")
            self.assertContains(response, "Completed")

    def test_admin_detail_renders_certificate_section(self):
        """Admin student detail renders certificate status."""
        GeneratedCertificate.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            certificate_number="DM-1", status=CERTIFICATE_STATUS_AVAILABLE,
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse("progression:admin_student_detail", args=[self.student.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "DM-1")
        self.assertContains(response, "Certificate")

    def test_student_progress_renders_completion_section(self):
        """Student progress page renders diploma completion status."""
        with override("en"):
            StudentDiplomaCompletion.objects.create(
                student=self.student, diploma_enrollment=self.enrollment,
                status="completed", completed_at=timezone.now(),
            )
            self.client.force_login(self.student_user)
            response = self.client.get(reverse("progression:student_progress"))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Diploma Status")
            self.assertContains(response, "Completed")

    def test_student_progress_renders_certificate_section(self):
        """Student progress page renders certificate status and download link."""
        GeneratedCertificate.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            certificate_number="DM-2", status=CERTIFICATE_STATUS_AVAILABLE,
        )
        self.client.force_login(self.student_user)
        response = self.client.get(reverse("progression:student_progress"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Certificate")
        self.assertContains(response, "DM-2")

    def test_student_progress_renders_decision_history(self):
        """Student progress page renders decision history when present."""
        PromotionDecision.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            decision_type=PROMOTION_DECISION_MANUAL_PROMOTE, reason="test",
        )
        self.client.force_login(self.student_user)
        response = self.client.get(reverse("progression:student_progress"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Decision History")
        self.assertContains(response, "Manual promote")

    def test_student_hides_revoked_certificates(self):
        """Student progress only shows available certificates."""
        GeneratedCertificate.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            certificate_number="DM-REVOKED", status=CERTIFICATE_STATUS_REVOKED,
        )
        self.client.force_login(self.student_user)
        response = self.client.get(reverse("progression:student_progress"))
        self.assertNotContains(response, "DM-REVOKED")

    def test_student_safe_lock_message(self):
        """Student progress renders a generic lock message, not the raw reason."""
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem1,
            status=SEMESTER_PROGRESS_LOCKED, lock_reason="Internal: academic dishonesty",
        )
        self.client.force_login(self.student_user)
        response = self.client.get(reverse("progression:student_progress"))
        self.assertContains(response, "locked")
        self.assertContains(response, "Contact your administrator")
        self.assertNotContains(response, "academic dishonesty")

    def test_admin_renders_requirement_summaries(self):
        """Admin detail renders missing/under_review/failed counts per semester."""
        Assessment.objects.create(
            semester=self.sem1, title="Test Assessment",
            assessment_type=ASSESSMENT_TYPE_QUIZ, total_points=Decimal("100"),
            passing_score=Decimal("50"),
            open_at=timezone.now(), close_at=timezone.now(),
            created_by=self.admin, is_active=True,
        )
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem1,
            status=SEMESTER_PROGRESS_IN_PROGRESS,
        )
        self.client.force_login(self.admin)
        with override("en"):
            response = self.client.get(reverse("progression:admin_student_detail", args=[self.student.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Semester 1")
        self.assertContains(response, "Missing")
        self.assertContains(response, "assessment(s)")

    def test_admin_renders_graduation_notes(self):
        """Admin detail renders graduation notes when present."""
        with override("en"):
            StudentDiplomaCompletion.objects.create(
                student=self.student, diploma_enrollment=self.enrollment,
                status="completed", completed_at=timezone.now(),
                graduation_notes="Awarded with distinction.",
            )
            self.client.force_login(self.admin)
            response = self.client.get(reverse("progression:admin_student_detail", args=[self.student.id]))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Awarded with distinction.")
