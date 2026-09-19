"""Tests for certificate services."""

from django.contrib.auth.models import Group
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import Cohort, Diploma, DiplomaEnrollment, Semester
from apps.accounts.constants import GROUP_ADMIN
from apps.accounts.models import User
from apps.certificates.models import CertificateTemplate
from apps.certificates.services import (
    generate_certificate_pdf,
    get_next_certificate_number,
    revoke_certificate,
)
from apps.progression.constants import (
    CERTIFICATE_STATUS_AVAILABLE,
    CERTIFICATE_STATUS_GENERATED,
    CERTIFICATE_STATUS_REVOKED,
    DIPLOMA_COMPLETION_COMPLETED,
)
from apps.progression.models import StudentDiplomaCompletion
from apps.students.models import StudentProfile
from apps.certificates.models import GeneratedCertificate
from apps.certificates.services import regenerate_certificate
from unittest.mock import patch
from apps.progression.models import PromotionDecision
from apps.certificates.models import CertificateCounter


def _create_user(email="student@test.com", group_name="Student"):
    user = User.objects.create_user(email=email, password="pass123")
    group, _ = Group.objects.get_or_create(name=group_name)
    user.groups.add(group)
    return user


class CertificateServiceTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(email="admin@test.com", password="pass123", is_staff=True)
        admin_group, _ = Group.objects.get_or_create(name=GROUP_ADMIN)
        self.admin.groups.add(admin_group)
        self.student_user = _create_user()
        self.student = StudentProfile.objects.create(
            user=self.student_user,
            full_name="Test Student",
            date_of_birth=timezone.now().date() - timezone.timedelta(days=365*20),
            gender="male",
            address="Test Address",
            phone="01234567890",
            national_id="12345678901234",
            education="bachelor",
        )
        self.diploma = Diploma.objects.create(title="Test Diploma", total_semesters=2, created_by=self.admin)
        self.sem1 = Semester.objects.create(diploma=self.diploma, order=1, title="Semester 1")
        self.sem2 = Semester.objects.create(diploma=self.diploma, order=2, title="Semester 2")
        self.cohort = Cohort.objects.create(
            diploma=self.diploma, name="Cohort 1", start_date=timezone.now().date(),
            created_by=self.admin,
        )
        self.enrollment = DiplomaEnrollment.objects.create(
            student=self.student, cohort=self.cohort, status=ENROLLMENT_STATUS_ACTIVE,
        )

    def test_get_next_certificate_number(self):
        num = get_next_certificate_number(self.diploma.id, self.student.id)
        self.assertTrue(num.startswith("DM-"))
        self.assertIn(str(self.diploma.id), num)
        self.assertIn(str(self.student.id), num)

    def test_cannot_generate_before_completion(self):
        with self.assertRaises(ValueError):
            generate_certificate_pdf(self.student, self.enrollment, actor=self.admin)

    def test_generates_placeholder_template(self):
        StudentDiplomaCompletion.objects.create(
            student=self.student,
            diploma_enrollment=self.enrollment,
            status=DIPLOMA_COMPLETION_COMPLETED,
            completed_at=timezone.now(),
        )

        template = CertificateTemplate.objects.filter(diploma=self.diploma).first()
        self.assertIsNone(template)

    def test_revoke_without_reason_raises(self):
        cert = GeneratedCertificate.objects.create(
            student=self.student,
            diploma_enrollment=self.enrollment,
            certificate_number="DM-2026-1-1-0001",
            status=CERTIFICATE_STATUS_GENERATED,
        )
        with self.assertRaises(ValueError):
            revoke_certificate(cert, actor=self.admin)

    def test_revoke_certificate(self):
        cert = GeneratedCertificate.objects.create(
            student=self.student,
            diploma_enrollment=self.enrollment,
            certificate_number="DM-2026-1-1-0002",
            status=CERTIFICATE_STATUS_AVAILABLE,
        )
        revoked = revoke_certificate(cert, actor=self.admin, reason="Administrative decision")
        self.assertEqual(revoked.status, CERTIFICATE_STATUS_REVOKED)
        self.assertIsNotNone(revoked.revoked_at)

    def test_certificate_number_unique(self):
        GeneratedCertificate.objects.create(
            student=self.student,
            diploma_enrollment=self.enrollment,
            certificate_number="DM-2026-1-1-0003",
            status=CERTIFICATE_STATUS_GENERATED,
        )
        with self.assertRaises(IntegrityError):
            GeneratedCertificate.objects.create(
                student=self.student,
                diploma_enrollment=self.enrollment,
                certificate_number="DM-2026-1-1-0003",
                status=CERTIFICATE_STATUS_GENERATED,
            )

    def test_regenerate_certificate_requires_reason(self):
        cert = GeneratedCertificate.objects.create(
            student=self.student,
            diploma_enrollment=self.enrollment,
            certificate_number="DM-2026-1-1-0100",
            status=CERTIFICATE_STATUS_AVAILABLE,
        )
        with self.assertRaises(ValueError, msg="Reason is required"):
            regenerate_certificate(cert, actor=self.admin)

    def test_regenerate_certificate_changes_status(self):
        StudentDiplomaCompletion.objects.create(
            student=self.student,
            diploma_enrollment=self.enrollment,
            status=DIPLOMA_COMPLETION_COMPLETED,
            completed_at=timezone.now(),
        )
        cert = GeneratedCertificate.objects.create(
            student=self.student,
            diploma_enrollment=self.enrollment,
            certificate_number="DM-2026-1-1-0101",
            status=CERTIFICATE_STATUS_AVAILABLE,
            pdf_storage_key="old/key.pdf",
        )
        with patch("apps.certificates.services._html_to_pdf", return_value=b"%PDF-dummy"):
            new_cert = regenerate_certificate(cert, actor=self.admin, reason="Lost original")
        self.assertEqual(new_cert.status, CERTIFICATE_STATUS_AVAILABLE)
        self.assertNotEqual(new_cert.pdf_storage_key, "old/key.pdf")
        self.assertTrue(PromotionDecision.objects.filter(reason="Lost original").exists())


class CertificateModelStringTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(email="admin@test.com", password="pass123", is_staff=True)
        admin_group, _ = Group.objects.get_or_create(name=GROUP_ADMIN)
        self.admin.groups.add(admin_group)
        self.diploma = Diploma.objects.create(title="Test Diploma", total_semesters=2, created_by=self.admin)

    def test_certificate_counter_str(self):
        counter = CertificateCounter.objects.create(diploma=self.diploma, year=2026, last_sequence=5)
        self.assertIn("Test Diploma", str(counter))
        self.assertIn("2026", str(counter))
        self.assertIn("5", str(counter))

    def test_certificate_template_str(self):
        template = CertificateTemplate.objects.create(
            name="My Template",
            diploma=self.diploma,
            language="ar",
            created_by=self.admin,
        )
        self.assertIn("My Template", str(template))
        self.assertIn("ar", str(template))

    def test_generated_certificate_str(self):
        student_user = User.objects.create_user(email="s@test.com", password="x")
        student = StudentProfile.objects.create(
            user=student_user, full_name="A Student",
            national_id="10000000000001", phone="01000000001",
            date_of_birth=timezone.now().date(),
        )
        cohort = Cohort.objects.create(
            diploma=self.diploma, name="C1", start_date=timezone.now().date(),
            created_by=self.admin,
        )
        enrollment = DiplomaEnrollment.objects.create(student=student, cohort=cohort, status=ENROLLMENT_STATUS_ACTIVE)
        cert = GeneratedCertificate.objects.create(
            student=student, diploma_enrollment=enrollment,
            certificate_number="DM-1-1-0001", status=CERTIFICATE_STATUS_GENERATED,
        )
        self.assertIn("DM-1-1-0001", str(cert))
        self.assertIn("A Student", str(cert))
