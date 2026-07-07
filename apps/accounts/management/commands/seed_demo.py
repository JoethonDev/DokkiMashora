"""Seed comprehensive demo data: enrollments, applications, assessments, payments, etc."""

from __future__ import annotations

import os
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE, ENROLLMENT_STATUS_EXPIRED
from apps.academics.models import (
    Cohort,
    Course,
    CourseEnrollment,
    CourseVersion,
    Diploma,
    DiplomaEnrollment,
    Semester,
)
from apps.applications.constants import (
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_REJECTED,
    PROGRAM_APP_STATUS_SUBMITTED,
    PROGRAM_TYPE_COURSE,
    PROGRAM_TYPE_DIPLOMA,
)
from apps.applications.models import ProgramApplication
from apps.assessments.constants import (
    ASSESSMENT_TYPE_HOMEWORK,
    ASSESSMENT_TYPE_QUIZ,
    ASSESSMENT_TYPE_SEMESTER_EXAM,
    ATTEMPT_STATUS_GRADED,
    ATTEMPT_STATUS_RELEASED,
    GRADE_VISIBILITY_RELEASED,
    HOMEWORK_STATUS_GRADED,
    QUESTION_TYPE_MCQ,
    QUESTION_TYPE_WRITTEN,
)
from apps.assessments.models import (
    Assessment,
    AssessmentAttempt,
    AssessmentChoice,
    AssessmentQuestion,
    HomeworkSubmission,
)
from apps.audit.models import AuditEvent
from apps.certificates.models import CertificateTemplate, GeneratedCertificate
from apps.communications.email_message import EmailMessage
from apps.communications.models import BulkEmailSend, EmailTemplate
from apps.exams.constants import ASSIGNMENT_STATUS_GRADED
from apps.exams.models import Answer, Assignment, Exam
from apps.payments.models import PaymentRecord, PaymentUpload, PaymentUploadRow
from apps.progression.constants import (
    CERTIFICATE_STATUS_AVAILABLE,
    PROMOTION_DECISION_AUTO_PROMOTE,
    SEMESTER_PROGRESS_PASSED,
)
from apps.progression.models import (
    DiplomaProgressionRule,
    PromotionDecision,
    SemesterProgressionRule,
    StudentDiplomaCompletion,
    StudentSemesterProgress,
)
from apps.students.models import StudentProfile

User = get_user_model()


class Command(BaseCommand):
    help = "Seed comprehensive demo data for all entity types."

    def handle(self, *args, **options):
        if os.environ.get("DJANGO_SEED_DEMO", "true").lower() in {"0", "false", "no"}:
            self.stdout.write("Demo data skipped (DJANGO_SEED_DEMO=false).")
            return

        admin = User.objects.filter(is_superuser=True).first()
        if not admin:
            self.stdout.write(self.style.ERROR("No superuser found. Run seed_roles first."))
            return

        teacher = User.objects.filter(email="teacher@dokkimashora.test").first()
        student_user = User.objects.filter(email="student@dokkimashora.test").first()
        if not student_user:
            self.stdout.write(self.style.ERROR("No demo student found. Run seed_roles first."))
            return

        diploma = Diploma.objects.filter(title="Diploma in Theological Studies").first()
        if not diploma:
            self.stdout.write(self.style.ERROR("No diploma found. Run seed_academics first."))
            return

        cohort = Cohort.objects.filter(diploma=diploma, name="2026 Spring").first()
        sem1 = Semester.objects.filter(diploma=diploma, order=1).first()
        sem2 = Semester.objects.filter(diploma=diploma, order=2).first()
        course_ot = Course.objects.filter(title="Old Testament Survey").first()
        course_nt = Course.objects.filter(title="New Testament Survey").first()

        profile, _ = StudentProfile.objects.get_or_create(
            user=student_user,
            defaults={
                "full_name": student_user.full_name,
                "national_id": "29801011234567",
                "country_code": "EG",
                "phone": "+201000000000",
                "gender": "male",
                "date_of_birth": date(1998, 1, 1),
                "address": "Cairo, Egypt",
                "education": "bachelor",
                "current_job": "Engineer",
            },
        )
        self._report("Student profile", _)

        # ── Program applications ──────────────────────────────────────
        _, created = ProgramApplication.objects.get_or_create(
            student=profile,
            program_type=PROGRAM_TYPE_DIPLOMA,
            cohort=cohort,
            status=PROGRAM_APP_STATUS_SUBMITTED,
        )
        self._report("Pending program application", created)

        _, created = ProgramApplication.objects.get_or_create(
            student=profile,
            program_type=PROGRAM_TYPE_COURSE,
            course=course_ot,
            status=PROGRAM_APP_STATUS_ACCEPTED,
            defaults={"reviewed_by": admin, "reviewed_at": timezone.now()},
        )
        self._report("Accepted program application", created)

        _, created = ProgramApplication.objects.get_or_create(
            student=profile,
            program_type=PROGRAM_TYPE_COURSE,
            course=course_nt,
            status=PROGRAM_APP_STATUS_REJECTED,
            defaults={"reviewed_by": admin, "rejection_reason": "Incomplete documents", "reviewed_at": timezone.now()},
        )
        self._report("Rejected program application", created)

        # ── Enrollments ───────────────────────────────────────────────

        diploma_enr, _ = DiplomaEnrollment.objects.get_or_create(
            student=profile, cohort=cohort,
            defaults={
                "status": ENROLLMENT_STATUS_ACTIVE,
                "activated_at": timezone.now(),
                "expires_at": cohort.end_date,
            },
        )
        self._report("Diploma enrollment", _)

        version_ot = CourseVersion.objects.filter(course=course_ot, cohort=cohort).first()
        version_nt = CourseVersion.objects.filter(course=course_nt, cohort=cohort).first()

        if version_ot:
            CourseEnrollment.objects.get_or_create(
                student=profile, course_version=version_ot,
                defaults={
                    "status": ENROLLMENT_STATUS_ACTIVE,
                    "activated_at": timezone.now(),
                    "expires_at": timezone.now() + timedelta(days=180),
                },
            )
            self._report("Course enrollment (OT)", True)

        if version_nt:
            CourseEnrollment.objects.get_or_create(
                student=profile, course_version=version_nt,
                defaults={
                    "status": ENROLLMENT_STATUS_EXPIRED,
                    "activated_at": timezone.now() - timedelta(days=200),
                    "expires_at": timezone.now() - timedelta(days=20),
                },
            )
            self._report("Course enrollment (NT expired)", True)

        # ── Entrance exam assignments ─────────────────────────────────
        entrance_exam = Exam.objects.filter(exam_type="entrance").first()
        if entrance_exam:
            Assignment.objects.get_or_create(
                exam=entrance_exam, student=student_user,
                defaults={"status": ASSIGNMENT_STATUS_GRADED, "score": 85},
            )
            self._report("Entrance exam assignment", True)

            for q in entrance_exam.questions.all():
                choice = q.choices.filter(is_correct=True).first()
                if choice:
                    Answer.objects.get_or_create(
                        assignment=Assignment.objects.filter(student=student_user).first(),
                        question=q,
                        defaults={"selected_choice": choice, "score": q.points},
                    )

        # ── Semester assessments ──────────────────────────────────────
        if sem1:
            quiz, _ = Assessment.objects.get_or_create(
                title="Old Testament Quiz 1",
                defaults={
                    "assessment_type": ASSESSMENT_TYPE_QUIZ,
                    "course_version": version_ot,
                    "semester": sem1,
                    "cohort": cohort,
                    "created_by": teacher or admin,
                    "open_at": timezone.now() - timedelta(days=14),
                    "close_at": timezone.now() + timedelta(days=7),
                    "duration_minutes": 30,
                    "total_points": 20,
                    "passing_score": 50,
                    "is_active": True,
                    "max_attempts": 2,
                    "show_correct_answers": True,
                    "grade_visibility": GRADE_VISIBILITY_RELEASED,
                },
            )
            self._report("Quiz assessment", _)

            mcq_q, _ = AssessmentQuestion.objects.get_or_create(
                assessment=quiz, question_type=QUESTION_TYPE_MCQ, order=1,
                defaults={"text": "How many books are in the Old Testament?", "points": 5},
            )
            if _:
                AssessmentChoice.objects.bulk_create([
                    AssessmentChoice(question=mcq_q, text="27", is_correct=False, order=1),
                    AssessmentChoice(question=mcq_q, text="39", is_correct=True, order=2),
                    AssessmentChoice(question=mcq_q, text="66", is_correct=False, order=3),
                ])

            written_q, _ = AssessmentQuestion.objects.get_or_create(
                assessment=quiz, question_type=QUESTION_TYPE_WRITTEN, order=2,
                defaults={"text": "Briefly explain the theme of the Pentateuch.", "points": 15},
            )

            attempt, _ = AssessmentAttempt.objects.get_or_create(
                assessment=quiz, student=student_user, attempt_number=1,
                defaults={
                    "status": ATTEMPT_STATUS_GRADED,
                    "started_at": timezone.now() - timedelta(days=10),
                    "submitted_at": timezone.now() - timedelta(days=10),
                    "graded_at": timezone.now() - timedelta(days=9),
                    "score_points": 18,
                    "score_percent": 90,
                    "is_pass": True,
                    "visible_to_student": True,
                },
            )

        if sem2:
            exam_assessment, _ = Assessment.objects.get_or_create(
                title="First Semester Final Exam",
                defaults={
                    "assessment_type": ASSESSMENT_TYPE_SEMESTER_EXAM,
                    "semester": sem1,
                    "cohort": cohort,
                    "created_by": teacher or admin,
                    "open_at": timezone.now() - timedelta(days=30),
                    "close_at": timezone.now() - timedelta(days=1),
                    "duration_minutes": 120,
                    "total_points": 100,
                    "passing_score": 60,
                    "is_active": True,
                    "max_attempts": 1,
                    "grade_visibility": GRADE_VISIBILITY_RELEASED,
                },
            )
            self._report("Semester exam", _)

            AssessmentAttempt.objects.get_or_create(
                assessment=exam_assessment, student=student_user, attempt_number=1,
                defaults={
                    "status": ATTEMPT_STATUS_RELEASED,
                    "started_at": timezone.now() - timedelta(days=2),
                    "submitted_at": timezone.now() - timedelta(days=2),
                    "graded_at": timezone.now() - timedelta(days=1),
                    "score_points": 78,
                    "score_percent": 78,
                    "is_pass": True,
                    "visible_to_student": True,
                },
            )
            self._report("Semester exam attempt", True)

        # ── Homework ──────────────────────────────────────────────────
        hw, _ = Assessment.objects.get_or_create(
            title="Homework: Church History Essay",
            defaults={
                "assessment_type": ASSESSMENT_TYPE_HOMEWORK,
                "course_version": CourseVersion.objects.filter(cohort=cohort).first(),
                "semester": sem2,
                "cohort": cohort,
                "created_by": teacher or admin,
                "open_at": timezone.now() - timedelta(days=7),
                "close_at": timezone.now() + timedelta(days=14),
                "duration_minutes": 0,
                "total_points": 50,
                "passing_score": 50,
                "is_active": True,
                "max_attempts": 1,
                "allow_late_submission": True,
                "penalty_percent": 10,
            },
        )
        self._report("Homework assessment", _)

        hw_submission, _ = HomeworkSubmission.objects.get_or_create(
            assessment=hw, student=student_user,
            defaults={
                "status": HOMEWORK_STATUS_GRADED,
                "submitted_at": timezone.now() - timedelta(days=3),
                "reviewed_at": timezone.now() - timedelta(days=2),
                "score": 42,
                "feedback": "Good work, well referenced.",
                "original_filename": "church_history_essay.pdf",
                "storage_key": "demo/hw/church_history_essay.pdf",
                "content_type": "application/pdf",
                "file_size": 102400,
                "checksum": "demo_checksum_123",
            },
        )

        # ── Progression rules ─────────────────────────────────────────
        prog_rule, _ = DiplomaProgressionRule.objects.get_or_create(
            diploma=diploma,
            defaults={
                "semester_pass_percent": 60,
                "final_exam_pass_percent": 60,
                "require_all_semesters_passed": True,
                "require_final_exam": True,
                "auto_promote_enabled": True,
                "allow_admin_override": True,
                "allow_retake": True,
                "max_retake_count": 2,
                "created_by": admin,
            },
        )
        self._report("Diploma progression rule", _)

        if sem1:
            SemesterProgressionRule.objects.get_or_create(
                diploma_rule=prog_rule, semester=sem1,
                defaults={
                    "pass_percent": 60,
                    "require_all_required_assessments": True,
                    "allow_missing_optional_assessments": True,
                },
            )
            self._report("Semester progression rule", True)

        if sem2:
            SemesterProgressionRule.objects.get_or_create(
                diploma_rule=prog_rule, semester=sem2,
                defaults={
                    "pass_percent": 60,
                    "require_all_required_assessments": True,
                    "allow_missing_optional_assessments": True,
                },
            )
            self._report("Semester 2 progression rule", True)

        # ── Student semester progress ─────────────────────────────────
        if sem1:
            StudentSemesterProgress.objects.get_or_create(
                student=profile, semester=sem1, cohort=cohort,
                diploma_enrollment=diploma_enr,
                defaults={
                    "status": SEMESTER_PROGRESS_PASSED,
                    "calculated_score": 85,
                    "passed_at": timezone.now() - timedelta(days=1),
                },
            )
            self._report("Semester 1 progress", True)

        # ── Promotion decision ─────────────────────────────────────────
        PromotionDecision.objects.get_or_create(
            student=profile, diploma_enrollment=diploma_enr,
            defaults={
                "decision_type": PROMOTION_DECISION_AUTO_PROMOTE,
                "from_semester": sem1,
                "to_semester": sem2,
                "automatic": True,
                "reason": "Semester passed with 85%",
            },
        )
        self._report("Promotion decision", True)

        # ── Diploma completion (only if both semesters exist) ─────────
        if sem2:
            StudentDiplomaCompletion.objects.get_or_create(
                student=profile, diploma_enrollment=diploma_enr,
                defaults={
                    "status": "completed",
                    "completed_at": timezone.now(),
                    "final_score": 82,
                    "completed_by": admin,
                },
            )
            self._report("Diploma completion", True)

        # ── Certificate template ──────────────────────────────────────
        cert_template, _ = CertificateTemplate.objects.get_or_create(
            diploma=diploma, name="Standard Diploma",
            defaults={
                "language": "ar",
                "template_type": "standard",
                "active": True,
                "created_by": admin,
            },
        )
        self._report("Certificate template", _)

        # ── Generated certificate ─────────────────────────────────────
        GeneratedCertificate.objects.get_or_create(
            student=profile,
            diploma_enrollment=diploma_enr,
            certificate_template=cert_template,
            defaults={
                "status": CERTIFICATE_STATUS_AVAILABLE,
                "certificate_number": "DEMO-2026-0001",
                "generated_by": admin,
                "generated_at": timezone.now(),
            },
        )
        self._report("Generated certificate", True)

        # ── Payment upload ────────────────────────────────────────────
        upload, _ = PaymentUpload.objects.get_or_create(
            original_filename="demo_payments.csv",
            defaults={
                "status": "confirmed",
                "total_rows": 2,
                "matched_rows": 2,
                "unmatched_rows": 0,
                "created_rows": 2,
                "updated_rows": 0,
                "failed_rows": 0,
                "uploaded_by": admin,
            },
        )
        self._report("Payment upload", _)

        PaymentUploadRow.objects.get_or_create(
            upload=upload, row_number=1,
            defaults={
                "raw_data": '{"national_id": "29801011234567", "amount": 500}',
                "normalized_national_id": "29801011234567",
                "amount": 500,
                "currency": "EGP",
                "paid_at": timezone.now() - timedelta(days=30),
                "external_reference": "DEMO-TXN-001",
                "status": "matched",
                "match_method": "national_id",
                "matched_student": profile,
            },
        )
        PaymentUploadRow.objects.get_or_create(
            upload=upload, row_number=2,
            defaults={
                "raw_data": '{"national_id": "29801011234567", "amount": 300}',
                "normalized_national_id": "29801011234567",
                "amount": 300,
                "currency": "EGP",
                "paid_at": timezone.now() - timedelta(days=15),
                "external_reference": "DEMO-TXN-002",
                "status": "matched",
                "match_method": "national_id",
                "matched_student": profile,
            },
        )

        # ── Payment records ───────────────────────────────────────────
        PaymentRecord.objects.get_or_create(
            student=profile, external_reference="DEMO-TXN-001",
            defaults={
                "amount": 500,
                "currency": "EGP",
                "status": "paid",
                "paid_at": timezone.now() - timedelta(days=30),
                "imported_by": admin,
            },
        )
        PaymentRecord.objects.get_or_create(
            student=profile, external_reference="DEMO-TXN-002",
            defaults={
                "amount": 300,
                "currency": "EGP",
                "status": "paid",
                "paid_at": timezone.now() - timedelta(days=15),
                "imported_by": admin,
            },
        )
        self._report("Payment records", True)

        # ── Audit events ──────────────────────────────────────────────
        AuditEvent.objects.get_or_create(
            actor=admin, action="demo.data_seeded",
            target_type="system",
            defaults={
                "ip_address": "127.0.0.1",
                "metadata": {"scope": "full_demo_seed"},
            },
        )
        self._report("Audit event", True)

        # ── Bulk email history ─────────────────────────────────────────

        email_tpl = EmailTemplate.objects.first()
        if email_tpl:
            bulk_send, _ = BulkEmailSend.objects.get_or_create(
                subject="Welcome to the Demo System",
                defaults={
                    "body": "This is a demo welcome email sent as part of seeding.",
                    "recipient_filter": "demo",
                    "recipient_emails": student_user.email,
                    "recipient_count": 1,
                    "success_count": 1,
                    "failure_count": 0,
                    "status": "sent",
                    "template": email_tpl,
                    "sent_by": admin,
                    "sent_at": timezone.now() - timedelta(days=30),
                },
            )
            self._report("Bulk email send", _)

            EmailMessage.objects.get_or_create(
                to_email=student_user.email, subject="Welcome to the Demo System",
                defaults={
                    "body": "Welcome! Your account has been created in the demo system.",
                    "status": "sent",
                    "sent_by": admin,
                    "sent_at": timezone.now() - timedelta(days=30),
                    "bulk_send": bulk_send,
                },
            )
            self._report("Email message", True)

        self.stdout.write(self.style.SUCCESS("Demo data seeded successfully."))

    def _report(self, label: str, created: bool) -> None:
        if created:
            self.stdout.write(self.style.SUCCESS(f"  Created: {label}"))
