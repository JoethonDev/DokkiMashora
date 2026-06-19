"""Tests for certificate views: permissions, URL routing, download."""
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import resolve, reverse
from django.utils import timezone

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import Cohort, Diploma, DiplomaEnrollment
from apps.accounts.constants import (
    GROUP_ADMIN,
    GROUP_MODERATOR,
    GROUP_STUDENT,
    GROUP_SUPER_ADMIN,
    GROUP_TEACHER,
)
from apps.accounts.models import User
from apps.certificates.models import GeneratedCertificate
from apps.certificates.views import admin_download_certificate, student_certificate_download
from apps.progression.constants import CERTIFICATE_STATUS_AVAILABLE
from apps.students.models import StudentProfile


def _create_user(email, group_name):
    user = User.objects.create_user(email=email, password="pass123")
    group, _ = Group.objects.get_or_create(name=group_name)
    user.groups.add(group)
    return user


class CertificateViewTests(TestCase):
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
        self.cert = GeneratedCertificate.objects.create(
            student=self.student,
            diploma_enrollment=self.enrollment,
            certificate_number="DM-2026-1-1-0001",
            status=CERTIFICATE_STATUS_AVAILABLE,
            pdf_storage_key="certificates/1/test.pdf",
        )

    def test_student_download_url_resolves(self):
        url = reverse("certificates:student_certificate_download", args=[self.cert.id])
        self.assertEqual(resolve(url).func, student_certificate_download)

    def test_admin_download_url_resolves(self):
        url = reverse("certificates:admin_download_certificate", args=[self.cert.id])
        self.assertEqual(resolve(url).func, admin_download_certificate)

    def test_student_download_own_cert(self):
        self.client.force_login(self.student_user)
        url = reverse("certificates:student_certificate_download", args=[self.cert.id])
        response = self.client.get(url)
        # Should fail because no PDF at that storage key (file not found)
        self.assertIn(response.status_code, (200, 302))

    def test_student_cannot_download_other_cert(self):
        other_student = _create_user("other@test.com", GROUP_STUDENT)
        StudentProfile.objects.create(
            user=other_student,
            full_name="Other",
            date_of_birth=timezone.now().date() - timezone.timedelta(days=365 * 20),
            gender="male",
            address="Addr",
            phone="01234567891",
            national_id="12345678901235",
            education="bachelor",
        )
        self.client.force_login(other_student)
        url = reverse("certificates:student_certificate_download", args=[self.cert.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_admin_download_any_cert(self):
        self.client.force_login(self.admin)
        url = reverse("certificates:admin_download_certificate", args=[self.cert.id])
        response = self.client.get(url)
        # Should redirect with error because no real PDF, but status is 302
        self.assertIn(response.status_code, (200, 302))

    def test_moderator_denied_admin_download(self):
        self.client.force_login(self.moderator)
        url = reverse("certificates:admin_download_certificate", args=[self.cert.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_teacher_denied_admin_download(self):
        self.client.force_login(self.teacher)
        url = reverse("certificates:admin_download_certificate", args=[self.cert.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_admin_template_list_allowed(self):
        for user in [self.super_admin, self.admin]:
            self.client.force_login(user)
            url = reverse("certificates:admin_template_list")
            response = self.client.get(url)
            self.assertIn(response.status_code, (200, 302), f"{user.email} should access template list")

    def test_moderator_denied_template_list(self):
        self.client.force_login(self.moderator)
        url = reverse("certificates:admin_template_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_teacher_denied_template_list(self):
        self.client.force_login(self.teacher)
        url = reverse("certificates:admin_template_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_admin_certificate_list_allowed(self):
        self.client.force_login(self.admin)
        url = reverse("certificates:admin_certificate_list")
        response = self.client.get(url)
        self.assertIn(response.status_code, (200, 302))

    def test_moderator_denied_certificate_list(self):
        self.client.force_login(self.moderator)
        url = reverse("certificates:admin_certificate_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_teacher_denied_certificate_list(self):
        self.client.force_login(self.teacher)
        url = reverse("certificates:admin_certificate_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)
