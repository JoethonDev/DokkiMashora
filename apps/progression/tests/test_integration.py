"""Integration tests for full progression and certificate lifecycle.

Covers:
- enrollment → semester assessment → promotion → final exam → completion → certificate → download
- Manual exception: fail → retake → pass → promote
- Lock lifecycle: lock → auto-promote skip → unlock → promote
"""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.test import TestCase
from django.utils import timezone

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import Cohort, Course, CourseVersion, Diploma, DiplomaEnrollment, Semester
from apps.accounts.constants import GROUP_ADMIN
from apps.accounts.models import User
from apps.assessments.constants import (
    ASSESSMENT_TYPE_FINAL_EXAM,
    ASSESSMENT_TYPE_QUIZ,
    ATTEMPT_STATUS_GRADED,
    ATTEMPT_STATUS_RELEASED,
    GRADE_VISIBILITY_RELEASED,
    QUESTION_TYPE_MCQ,
)
from apps.assessments.models import Assessment, AssessmentAttempt, AssessmentChoice, AssessmentQuestion
from apps.certificates.services import generate_certificate_pdf
from apps.progression.constants import (
    CERTIFICATE_STATUS_AVAILABLE,
    DIPLOMA_COMPLETION_COMPLETED,
    DIPLOMA_COMPLETION_GRADUATED,
    DIPLOMA_COMPLETION_REQUIREMENTS_MET,
    PROMOTION_DECISION_COMPLETE,
    PROMOTION_DECISION_DEMOTE,
    PROMOTION_DECISION_GRADUATE,
    PROMOTION_DECISION_LOCK,
    PROMOTION_DECISION_MANUAL_PROMOTE,
    PROMOTION_DECISION_RETAKE_ASSIGNED,
    PROMOTION_DECISION_RETAKE_CLEARED,
    PROMOTION_DECISION_UNLOCK,
    SEMESTER_PROGRESS_DEMOTED,
    SEMESTER_PROGRESS_FAILED,
    SEMESTER_PROGRESS_IN_PROGRESS,
    SEMESTER_PROGRESS_LOCKED,
    SEMESTER_PROGRESS_PASSED,
    SEMESTER_PROGRESS_PROMOTED,
    SEMESTER_PROGRESS_RETAKE_REQUIRED,
)
from apps.progression.models import (
    DiplomaProgressionRule,
    PromotionDecision,
    SemesterProgressionRule,
    StudentDiplomaCompletion,
    StudentSemesterProgress,
)
from apps.progression.services import (
    assign_retake,
    auto_promote_students,
    check_final_exam_eligibility,
    clear_retake,
    demote_student,
    evaluate_semester_progress,
    lock_student,
    manual_promote,
    mark_completed,
    mark_graduated,
    mark_requirements_met,
    unlock_student,
)
from apps.storage.services import storage_service
from apps.students.models import StudentProfile


def _create_user(email="student@test.com", group_name="Student"):
    user = User.objects.create_user(email=email, password="pass123")
    group, _ = Group.objects.get_or_create(name=group_name)
    user.groups.add(group)
    return user


def _create_admin(email="admin@test.com"):
    user = User.objects.create_user(email=email, password="pass123", is_staff=True)
    group, _ = Group.objects.get_or_create(name=GROUP_ADMIN)
    user.groups.add(group)
    return user


class FullLifecycleIntegrationTests(TestCase):
    """Integration tests covering the complete student lifecycle."""

    def setUp(self):
        self.admin = _create_admin()
        self.student_user = _create_user()
        self.student = StudentProfile.objects.create(
            user=self.student_user,
            full_name="Test Student",
            date_of_birth=timezone.now().date() - timezone.timedelta(days=365 * 20),
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
            diploma=self.diploma, name="Cohort 1",
            start_date=timezone.now().date(), created_by=self.admin,
        )
        self.enrollment = DiplomaEnrollment.objects.create(
            student=self.student, cohort=self.cohort, status=ENROLLMENT_STATUS_ACTIVE,
        )
        self.rule, _ = DiplomaProgressionRule.objects.update_or_create(
            diploma=self.diploma,
            defaults={
                "semester_pass_percent": Decimal("50.00"),
                "final_exam_pass_percent": Decimal("60.00"),
                "require_all_semesters_passed": True,
                "require_final_exam": True,
                "auto_promote_enabled": True,
                "allow_admin_override": True,
                "allow_retake": True,
                "max_retake_count": 3,
                "is_active": True,
                "created_by": self.admin,
            },
        )
        self.course = Course.objects.create(title="Test Course", created_by=self.admin)
        self.cv = CourseVersion.objects.create(
            course=self.course, cohort=self.cohort, semester=self.sem1,
            created_by=self.admin,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _create_assessment(self, semester, atype=ASSESSMENT_TYPE_QUIZ, total_points=Decimal("100.00")):
        return Assessment.objects.create(
            title=f"Test {atype}",
            assessment_type=atype,
            semester=semester,
            cohort=self.cohort,
            course_version=self.cv,
            open_at=timezone.now() - timezone.timedelta(days=1),
            close_at=timezone.now() + timezone.timedelta(days=1),
            total_points=total_points,
            passing_score=Decimal("50.00"),
            max_attempts=1,
            is_active=True,
            grade_visibility=GRADE_VISIBILITY_RELEASED,
            created_by=self.admin,
        )

    def _create_mcq_question(self, assessment):
        q = AssessmentQuestion.objects.create(
            assessment=assessment, text="Q1", question_type=QUESTION_TYPE_MCQ,
            points=Decimal("10.00"), order=1, required=True,
        )
        AssessmentChoice.objects.create(question=q, text="Correct", is_correct=True, order=1)
        AssessmentChoice.objects.create(question=q, text="Wrong", is_correct=False, order=2)
        return q

    def _create_attempt(self, assessment, status=ATTEMPT_STATUS_RELEASED, score_pct=Decimal("80.00"), score_pts=Decimal("80.00")):
        attempt = AssessmentAttempt.objects.create(
            assessment=assessment,
            student=self.student_user,
            attempt_number=1,
            status=status,
            score_points=score_pts,
            score_percent=score_pct,
            is_pass=score_pct >= assessment.passing_score if score_pct is not None else False,
            visible_to_student=True,
            submitted_at=timezone.now(),
        )
        if status in (ATTEMPT_STATUS_GRADED, ATTEMPT_STATUS_RELEASED):
            attempt.graded_at = timezone.now()
            attempt.save(update_fields=["graded_at"])
        return attempt

    def _pass_semester(self, semester):
        a = self._create_assessment(semester)
        self._create_mcq_question(a)
        self._create_attempt(a)
        return evaluate_semester_progress(self.student, self.enrollment, semester)

    def _promote_semester(self, semester):
        return manual_promote(
            self.student, self.enrollment, semester,
            actor=self.admin, reason="Manual promotion",
        )

    # ------------------------------------------------------------------
    # Full lifecycle with final exam
    # ------------------------------------------------------------------

    def test_full_lifecycle_with_final_exam(self):
        """Enrolled student → passes semesters → final exam → completed → certificate → download."""
        # 1. Pass both semesters
        p1 = self._pass_semester(self.sem1)
        self.assertEqual(p1.status, SEMESTER_PROGRESS_PASSED)
        self.assertIsNotNone(p1.calculated_score)
        self.assertIsNotNone(p1.calculated_at)

        p2 = self._pass_semester(self.sem2)
        self.assertEqual(p2.status, SEMESTER_PROGRESS_PASSED)

        # 2. Verify final exam eligibility
        eligibility = check_final_exam_eligibility(self.student, self.enrollment)
        self.assertTrue(eligibility.eligible)
        self.assertEqual(eligibility.blocked_reason, "")

        # 3. Create and pass final exam
        final_assessment = self._create_assessment(self.sem2, atype=ASSESSMENT_TYPE_FINAL_EXAM)
        self._create_mcq_question(final_assessment)
        final_attempt = self._create_attempt(final_assessment, score_pct=Decimal("85.00"), score_pts=Decimal("85.00"))
        eligibility.final_assessment = final_assessment
        eligibility.final_attempt = final_attempt
        eligibility.passed_final_exam = True
        eligibility.reviewed_by = self.admin
        eligibility.reviewed_at = timezone.now()
        eligibility.save()

        # 4. Mark completed
        completion = mark_completed(self.student, self.enrollment, actor=self.admin, notes="All done")
        self.assertEqual(completion.status, DIPLOMA_COMPLETION_COMPLETED)

        # Verify completion decision recorded
        self.assertTrue(
            PromotionDecision.objects.filter(
                student=self.student, decision_type=PROMOTION_DECISION_COMPLETE,
            ).exists()
        )

        # 5. Generate certificate (mock PDF to avoid external library dependency)
        with patch("apps.certificates.services._html_to_pdf", return_value=b"%PDF-dummy-data"):
            cert = generate_certificate_pdf(self.student, self.enrollment, actor=self.admin)

        self.assertEqual(cert.status, CERTIFICATE_STATUS_AVAILABLE)
        self.assertTrue(cert.certificate_number.startswith("DM-"))
        self.assertIsNotNone(cert.pdf_storage_key)
        self.assertIsNotNone(cert.pdf_file_size)

        # 6. Verify stored PDF is downloadable
        pdf_bytes = storage_service.download_file(cert.pdf_storage_key)
        self.assertEqual(pdf_bytes, b"%PDF-dummy-data")

    # ------------------------------------------------------------------
    # Full lifecycle without final exam
    # ------------------------------------------------------------------

    def test_full_lifecycle_without_final_exam(self):
        """All semesters passed → graduate → certificate (no final exam required)."""
        self.rule.require_final_exam = False
        self.rule.save()

        self._pass_semester(self.sem1)
        self._pass_semester(self.sem2)

        completion = mark_graduated(self.student, self.enrollment, actor=self.admin, notes="Graduated")
        self.assertEqual(completion.status, DIPLOMA_COMPLETION_GRADUATED)

        self.assertTrue(
            PromotionDecision.objects.filter(
                student=self.student, decision_type=PROMOTION_DECISION_GRADUATE,
            ).exists()
        )

        with patch("apps.certificates.services._html_to_pdf", return_value=b"%PDF-dummy"):
            cert = generate_certificate_pdf(self.student, self.enrollment, actor=self.admin)

        self.assertEqual(cert.status, CERTIFICATE_STATUS_AVAILABLE)
        self.assertIsNotNone(cert.pdf_storage_key)

        pdf_bytes = storage_service.download_file(cert.pdf_storage_key)
        self.assertEqual(pdf_bytes, b"%PDF-dummy")

    # ------------------------------------------------------------------
    # Auto-promote lifecycle
    # ------------------------------------------------------------------

    def test_auto_promote_lifecycle(self):
        """Auto-promote student through semesters with passing scores."""
        self.rule.auto_promote_enabled = True
        self.rule.save()

        # Create assessments + passing attempts for both semesters
        a1 = self._create_assessment(self.sem1)
        self._create_mcq_question(a1)
        self._create_attempt(a1)

        a2 = self._create_assessment(self.sem2)
        self._create_mcq_question(a2)
        self._create_attempt(a2)

        # First auto-promote call: promotes sem1 → sem2
        results = auto_promote_students(cohort=self.cohort)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["promoted"])
        self.assertEqual(results[0]["reason"], "Promoted")
        self.assertEqual(results[0]["from_semester"], self.sem1.id)

        # Verify sem1 is promoted and sem2 is in progress
        sem1_progress = StudentSemesterProgress.objects.get(
            diploma_enrollment=self.enrollment, semester=self.sem1,
        )
        self.assertEqual(sem1_progress.status, SEMESTER_PROGRESS_PROMOTED)

        sem2_progress = StudentSemesterProgress.objects.get(
            diploma_enrollment=self.enrollment, semester=self.sem2,
        )
        self.assertEqual(sem2_progress.status, SEMESTER_PROGRESS_IN_PROGRESS)

        # Auto decision recorded
        self.assertTrue(
            PromotionDecision.objects.filter(
                student=self.student, decision_type="auto_promote", automatic=True,
            ).exists()
        )

    # ------------------------------------------------------------------
    # Manual exception lifecycle: fail → retake → pass → promote
    # ------------------------------------------------------------------

    def test_exception_lifecycle_fail_retake_promote(self):
        """Student fails → admin assigns retake → student passes → admin clears retake → promote."""
        # 1. Create assessment with failing score
        a = self._create_assessment(self.sem1)
        self._create_mcq_question(a)
        self._create_attempt(a, score_pct=Decimal("30.00"), score_pts=Decimal("30.00"))
        progress = evaluate_semester_progress(self.student, self.enrollment, self.sem1)
        self.assertEqual(progress.status, SEMESTER_PROGRESS_FAILED)

        # 2. Admin assigns retake
        retake_progress = assign_retake(
            self.student, self.enrollment, self.sem1,
            actor=self.admin, reason="Needs retake",
        )
        self.assertTrue(retake_progress.retake_required)
        self.assertEqual(retake_progress.retake_count, 1)
        self.assertEqual(retake_progress.status, SEMESTER_PROGRESS_RETAKE_REQUIRED)

        self.assertTrue(
            PromotionDecision.objects.filter(
                student=self.student, decision_type=PROMOTION_DECISION_RETAKE_ASSIGNED,
            ).exists()
        )

        # 3. Student passes retake (new attempt with passing score, attempt_number=2)
        AssessmentAttempt.objects.create(
            assessment=a,
            student=self.student_user,
            attempt_number=2,
            status=ATTEMPT_STATUS_RELEASED,
            score_points=Decimal("75.00"),
            score_percent=Decimal("75.00"),
            is_pass=True,
            visible_to_student=True,
            submitted_at=timezone.now(),
            graded_at=timezone.now(),
        )

        # 4. Admin clears retake
        cleared = clear_retake(
            self.student, self.enrollment, self.sem1,
            actor=self.admin, reason="Passed retake",
        )
        self.assertFalse(cleared.retake_required)
        self.assertEqual(cleared.status, SEMESTER_PROGRESS_IN_PROGRESS)

        self.assertTrue(
            PromotionDecision.objects.filter(
                student=self.student, decision_type=PROMOTION_DECISION_RETAKE_CLEARED,
            ).exists()
        )

        # 5. Re-evaluate to get passing status
        evaluate_semester_progress(self.student, self.enrollment, self.sem1)
        sem1_progress = StudentSemesterProgress.objects.get(
            diploma_enrollment=self.enrollment, semester=self.sem1,
        )
        self.assertEqual(sem1_progress.status, SEMESTER_PROGRESS_PASSED)

        # 6. Manual promote
        promoted = manual_promote(
            self.student, self.enrollment, self.sem1,
            actor=self.admin, reason="Passed retake, promoting",
        )
        self.assertEqual(promoted.status, SEMESTER_PROGRESS_PROMOTED)

        self.assertTrue(
            PromotionDecision.objects.filter(
                student=self.student, decision_type=PROMOTION_DECISION_MANUAL_PROMOTE,
            ).exists()
        )

        # Verify decision history preserved
        decisions = PromotionDecision.objects.filter(student=self.student).order_by("decided_at")
        self.assertEqual(decisions.count(), 3)
        self.assertEqual(decisions[0].decision_type, PROMOTION_DECISION_RETAKE_ASSIGNED)
        self.assertEqual(decisions[1].decision_type, PROMOTION_DECISION_RETAKE_CLEARED)
        self.assertEqual(decisions[2].decision_type, PROMOTION_DECISION_MANUAL_PROMOTE)

    # ------------------------------------------------------------------
    # Lock lifecycle
    # ------------------------------------------------------------------

    def test_lock_lifecycle(self):
        """Locked student skipped by auto-promote → unlock → promote proceeds."""
        self.rule.auto_promote_enabled = True
        self.rule.save()

        a = self._create_assessment(self.sem1)
        self._create_mcq_question(a)
        self._create_attempt(a)
        evaluate_semester_progress(self.student, self.enrollment, self.sem1)

        # 1. Lock student
        lock_student(self.student, self.enrollment, actor=self.admin, reason="Academic hold")
        sem1 = StudentSemesterProgress.objects.get(
            diploma_enrollment=self.enrollment, semester=self.sem1,
        )
        self.assertEqual(sem1.status, SEMESTER_PROGRESS_LOCKED)

        self.assertTrue(
            PromotionDecision.objects.filter(
                student=self.student, decision_type=PROMOTION_DECISION_LOCK,
            ).exists()
        )

        # 2. Auto-promote should skip locked student
        results = auto_promote_students(cohort=self.cohort)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["promoted"])
        self.assertEqual(results[0]["reason"], "Locked")

        # 3. Unlock student
        unlock_student(self.student, self.enrollment, actor=self.admin, reason="Hold resolved")
        sem1.refresh_from_db()
        self.assertNotEqual(sem1.status, SEMESTER_PROGRESS_LOCKED)

        self.assertTrue(
            PromotionDecision.objects.filter(
                student=self.student, decision_type=PROMOTION_DECISION_UNLOCK,
            ).exists()
        )

        # 4. Re-evaluate and promote
        evaluate_semester_progress(self.student, self.enrollment, self.sem1)
        sem1.refresh_from_db()
        self.assertEqual(sem1.status, SEMESTER_PROGRESS_PASSED)

        self._promote_semester(self.sem1)
        sem1.refresh_from_db()
        self.assertEqual(sem1.status, SEMESTER_PROGRESS_PROMOTED)

    # ------------------------------------------------------------------
    # Demotion lifecycle
    # ------------------------------------------------------------------

    def test_promote_then_demote(self):
        """Promote student, then demote back with history preserved."""
        self._pass_semester(self.sem1)

        # Promote sem1 (marks it PROMOTED, creates sem2 as IN_PROGRESS)
        self._promote_semester(self.sem1)
        sem1 = StudentSemesterProgress.objects.get(
            diploma_enrollment=self.enrollment, semester=self.sem1,
        )
        self.assertEqual(sem1.status, SEMESTER_PROGRESS_PROMOTED)

        # Now demote from sem2 back to sem1
        prev = demote_student(
            self.student, self.enrollment, self.sem2,
            actor=self.admin, reason="Needs improvement",
        )
        self.assertIsNotNone(prev)

        self.assertTrue(
            PromotionDecision.objects.filter(
                student=self.student, decision_type=PROMOTION_DECISION_DEMOTE,
            ).exists()
        )

    # ------------------------------------------------------------------
    # Certificate duplicate prevention
    # ------------------------------------------------------------------

    def test_certificate_duplicate_prevention(self):
        """Cannot generate a second active certificate for the same enrollment."""
        self.rule.require_final_exam = False
        self.rule.save()

        self._pass_semester(self.sem1)
        self._pass_semester(self.sem2)
        mark_graduated(self.student, self.enrollment, actor=self.admin, notes="Done")

        with patch("apps.certificates.services._html_to_pdf", return_value=b"%PDF"):
            cert1 = generate_certificate_pdf(self.student, self.enrollment, actor=self.admin)
            self.assertEqual(cert1.status, CERTIFICATE_STATUS_AVAILABLE)

            # Second attempt should raise
            with self.assertRaises(ValueError):
                generate_certificate_pdf(self.student, self.enrollment, actor=self.admin)

    # ------------------------------------------------------------------
    # Final exam eligibility blocked by lock
    # ------------------------------------------------------------------

    def test_final_exam_blocked_by_lock(self):
        """Locked student is not final-exam eligible."""
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem1, status=SEMESTER_PROGRESS_LOCKED,
        )
        eligibility = check_final_exam_eligibility(self.student, self.enrollment)
        self.assertFalse(eligibility.eligible)
        self.assertIn("locked", eligibility.blocked_reason.lower())

    # ------------------------------------------------------------------
    # Completion blocked by retake
    # ------------------------------------------------------------------

    def test_completion_blocked_by_retake(self):
        """Cannot complete with unresolved retake."""
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem1, status=SEMESTER_PROGRESS_PASSED,
        )
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem2, status=SEMESTER_PROGRESS_PASSED,
            retake_required=True,
        )
        with self.assertRaises(ValueError):
            mark_completed(self.student, self.enrollment, actor=self.admin)

    # ------------------------------------------------------------------
    # Regression: rule-driven pass_threshold
    # ------------------------------------------------------------------

    def test_semester_rule_pass_threshold_enforced(self):
        """SemesterProgressionRule custom threshold is honored over the diploma default."""
        # Create a SemesterProgressionRule with 80% threshold
        SemesterProgressionRule.objects.create(
            diploma_rule=self.rule,
            semester=self.sem1,
            pass_percent=Decimal("80.00"),
            is_active=True,
        )

        a = self._create_assessment(self.sem1)
        self._create_mcq_question(a)
        # 70% is below the 80% rule threshold
        self._create_attempt(a, score_pct=Decimal("70.00"), score_pts=Decimal("70.00"))
        progress = evaluate_semester_progress(self.student, self.enrollment, self.sem1)
        self.assertEqual(progress.status, SEMESTER_PROGRESS_FAILED)

    def test_diploma_rule_pass_threshold_fallback(self):
        """Without SemesterProgressionRule, the diploma rule threshold applies."""
        self.rule.semester_pass_percent = Decimal("70.00")
        self.rule.save()

        a = self._create_assessment(self.sem1)
        self._create_mcq_question(a)
        # 60% is below the 70% diploma rule threshold
        self._create_attempt(a, score_pct=Decimal("60.00"), score_pts=Decimal("60.00"))
        progress = evaluate_semester_progress(self.student, self.enrollment, self.sem1)
        self.assertEqual(progress.status, SEMESTER_PROGRESS_FAILED)

        # 75% passes (new attempt, attempt_number=2)
        AssessmentAttempt.objects.create(
            assessment=a,
            student=self.student_user,
            attempt_number=2,
            status=ATTEMPT_STATUS_RELEASED,
            score_points=Decimal("75.00"),
            score_percent=Decimal("75.00"),
            is_pass=True,
            visible_to_student=True,
            submitted_at=timezone.now(),
            graded_at=timezone.now(),
        )
        progress = evaluate_semester_progress(self.student, self.enrollment, self.sem1)
        self.assertEqual(progress.status, SEMESTER_PROGRESS_PASSED)

    # ------------------------------------------------------------------
    # Regression: audit previous_status correctness
    # ------------------------------------------------------------------

    def test_audit_previous_status_on_promote(self):
        """PromotionDecision records the correct pre-mutation status."""
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem1, status=SEMESTER_PROGRESS_PASSED,
        )
        manual_promote(self.student, self.enrollment, self.sem1, actor=self.admin, reason="Promote")
        decision = PromotionDecision.objects.filter(
            decision_type=PROMOTION_DECISION_MANUAL_PROMOTE,
        ).first()
        self.assertIsNotNone(decision)
        self.assertEqual(decision.previous_status, SEMESTER_PROGRESS_PASSED)
        self.assertEqual(decision.new_status, SEMESTER_PROGRESS_PROMOTED)

    def test_audit_previous_status_on_demote(self):
        """Demotion records the correct pre-mutation status."""
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem2, status=SEMESTER_PROGRESS_PASSED,
        )
        demote_student(self.student, self.enrollment, self.sem2, actor=self.admin, reason="Demote")
        decision = PromotionDecision.objects.filter(
            decision_type=PROMOTION_DECISION_DEMOTE,
        ).first()
        self.assertIsNotNone(decision)
        self.assertEqual(decision.previous_status, SEMESTER_PROGRESS_PASSED)
        self.assertEqual(decision.new_status, SEMESTER_PROGRESS_DEMOTED)

    def test_audit_previous_status_on_lock(self):
        """Lock records the correct pre-mutation status."""
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem1, status=SEMESTER_PROGRESS_PASSED,
        )
        lock_student(self.student, self.enrollment, actor=self.admin, reason="Lock")
        decision = PromotionDecision.objects.filter(
            decision_type=PROMOTION_DECISION_LOCK,
        ).first()
        self.assertIsNotNone(decision)
        self.assertEqual(decision.previous_status, SEMESTER_PROGRESS_PASSED)
        self.assertEqual(decision.new_status, SEMESTER_PROGRESS_LOCKED)

    def test_audit_previous_status_on_retake(self):
        """Retake assign records the correct pre-mutation status."""
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem1, status=SEMESTER_PROGRESS_FAILED,
        )
        assign_retake(self.student, self.enrollment, self.sem1, actor=self.admin, reason="Retake")
        decision = PromotionDecision.objects.filter(
            decision_type=PROMOTION_DECISION_RETAKE_ASSIGNED,
        ).first()
        self.assertIsNotNone(decision)
        self.assertEqual(decision.previous_status, SEMESTER_PROGRESS_FAILED)
        self.assertEqual(decision.new_status, SEMESTER_PROGRESS_RETAKE_REQUIRED)

    def test_audit_previous_status_on_complete(self):
        """Mark complete records the correct pre-mutation status."""
        self.rule.require_final_exam = False
        self.rule.save()
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem1, status=SEMESTER_PROGRESS_PASSED,
        )
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem2, status=SEMESTER_PROGRESS_PASSED,
        )
        mark_completed(self.student, self.enrollment, actor=self.admin, notes="Done")
        decision = PromotionDecision.objects.filter(
            decision_type=PROMOTION_DECISION_COMPLETE,
        ).first()
        self.assertIsNotNone(decision)
        self.assertEqual(decision.previous_status, DIPLOMA_COMPLETION_REQUIREMENTS_MET)
        self.assertEqual(decision.new_status, DIPLOMA_COMPLETION_COMPLETED)

    def test_audit_previous_status_on_graduate(self):
        """Mark graduate records the correct pre-mutation status."""
        self.rule.require_final_exam = False
        self.rule.save()
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem1, status=SEMESTER_PROGRESS_PASSED,
        )
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem2, status=SEMESTER_PROGRESS_PASSED,
        )
        mark_graduated(self.student, self.enrollment, actor=self.admin, notes="Grad")
        decision = PromotionDecision.objects.filter(
            decision_type=PROMOTION_DECISION_GRADUATE,
        ).first()
        self.assertIsNotNone(decision)
        self.assertEqual(decision.previous_status, DIPLOMA_COMPLETION_REQUIREMENTS_MET)
        self.assertEqual(decision.new_status, DIPLOMA_COMPLETION_GRADUATED)

    # ------------------------------------------------------------------
    # Regression: validation ordering (no orphan rows)
    # ------------------------------------------------------------------

    def test_failed_retake_leaves_no_orphan_progress(self):
        """Rejected retake due to max count does not create/update progress."""
        self.rule.max_retake_count = 1
        self.rule.save()
        # Create progress that already has max retakes
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem1, status=SEMESTER_PROGRESS_RETAKE_REQUIRED,
            retake_required=True, retake_count=1,
        )
        with self.assertRaises(ValueError):
            assign_retake(self.student, self.enrollment, self.sem1, actor=self.admin, reason="Should fail")
        # Verify retake_count was not incremented
        p = StudentSemesterProgress.objects.get(
            diploma_enrollment=self.enrollment, semester=self.sem1,
        )
        self.assertEqual(p.retake_count, 1)

    def test_failed_completion_leaves_no_orphan_completion(self):
        """Rejected completion due to missing requirements does not create a completion record."""
        with self.assertRaises(ValueError):
            mark_requirements_met(self.student, self.enrollment)
        self.assertFalse(
            StudentDiplomaCompletion.objects.filter(
                diploma_enrollment=self.enrollment,
            ).exists()
        )
