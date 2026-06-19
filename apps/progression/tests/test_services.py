"""Tests for progression services."""

from decimal import Decimal

from django.contrib.auth.models import Group
from django.test import TestCase
from django.utils import timezone

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import (
    Cohort,
    Course,
    CourseVersion,
    Diploma,
    DiplomaEnrollment,
    Semester,
)
from apps.accounts.constants import GROUP_ADMIN
from apps.accounts.models import User
from apps.assessments.constants import (
    ASSESSMENT_TYPE_QUIZ,
    ATTEMPT_STATUS_GRADED,
    ATTEMPT_STATUS_RELEASED,
    ATTEMPT_STATUS_UNDER_REVIEW,
    GRADE_VISIBILITY_RELEASED,
    QUESTION_TYPE_MCQ,
)
from apps.assessments.models import Assessment, AssessmentAttempt, AssessmentChoice, AssessmentQuestion
from apps.progression.constants import (
    SEMESTER_PROGRESS_FAILED,
    SEMESTER_PROGRESS_IN_PROGRESS,
    SEMESTER_PROGRESS_LOCKED,
    SEMESTER_PROGRESS_NOT_STARTED,
    SEMESTER_PROGRESS_PASSED,
    SEMESTER_PROGRESS_PROMOTED,
    SEMESTER_PROGRESS_RETAKE_REQUIRED,
)
from apps.progression.models import (
    DiplomaProgressionRule,
    PromotionDecision,
    StudentSemesterProgress,
)
from apps.progression.services import (
    aggregate_semester_scores,
    assign_retake,
    check_final_exam_eligibility,
    clear_retake,
    demote_student,
    evaluate_semester_progress,
    lock_student,
    manual_promote,
    mark_completed,
    mark_graduated,
    mark_requirements_met,
    override_student_progress,
    unlock_student,
)
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


class ProgressionServiceTests(TestCase):
    def setUp(self):
        self.admin = _create_admin()
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
        self.rule = DiplomaProgressionRule.objects.create(
            diploma=self.diploma,
            semester_pass_percent=Decimal("50.00"),
            final_exam_pass_percent=Decimal("60.00"),
            require_all_semesters_passed=True,
            require_final_exam=True,
            auto_promote_enabled=False,
            allow_admin_override=True,
            allow_retake=True,
            max_retake_count=3,
            is_active=True,
            created_by=self.admin,
        )
        self.course = Course.objects.create(title="Test Course", created_by=self.admin)
        self.cv = CourseVersion.objects.create(
            course=self.course, cohort=self.cohort, semester=self.sem1, created_by=self.admin,
        )

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
            is_pass=score_pct >= assessment.passing_score if score_pct else False,
            visible_to_student=True,
            submitted_at=timezone.now(),
        )
        if status in (ATTEMPT_STATUS_GRADED, ATTEMPT_STATUS_RELEASED):
            attempt.graded_at = timezone.now()
            attempt.save(update_fields=["graded_at"])
        return attempt

    # ------------------------------------------------------------------
    # Score aggregation
    # ------------------------------------------------------------------

    def test_aggregate_no_assessments(self):
        result = aggregate_semester_scores(self.student, self.sem1)
        self.assertFalse(result.passed)
        self.assertEqual(result.score_percent, Decimal("0.00"))

    def test_aggregate_all_passed(self):
        a = self._create_assessment(self.sem1)
        self._create_mcq_question(a)
        self._create_attempt(a)
        result = aggregate_semester_scores(self.student, self.sem1)
        self.assertTrue(result.passed)
        self.assertTrue(result.score_percent > Decimal("0"))

    def test_aggregate_under_review(self):
        a = self._create_assessment(self.sem1)
        self._create_mcq_question(a)
        self._create_attempt(a, status=ATTEMPT_STATUS_UNDER_REVIEW, score_pct=None, score_pts=None)
        result = aggregate_semester_scores(self.student, self.sem1)
        self.assertTrue(result.under_review)
        self.assertFalse(result.passed)

    # ------------------------------------------------------------------
    # Semester evaluation
    # ------------------------------------------------------------------

    def test_evaluate_passed(self):
        a = self._create_assessment(self.sem1)
        self._create_mcq_question(a)
        self._create_attempt(a)
        progress = evaluate_semester_progress(self.student, self.enrollment, self.sem1)
        self.assertEqual(progress.status, SEMESTER_PROGRESS_PASSED)

    def test_evaluate_failed(self):
        a = self._create_assessment(self.sem1)
        self._create_mcq_question(a)
        self._create_attempt(a, score_pct=Decimal("10.00"), score_pts=Decimal("10.00"))
        progress = evaluate_semester_progress(self.student, self.enrollment, self.sem1)
        self.assertEqual(progress.status, SEMESTER_PROGRESS_FAILED)

    def test_evaluate_missing_assessment(self):
        self._create_assessment(self.sem1)
        progress = evaluate_semester_progress(self.student, self.enrollment, self.sem1)
        self.assertIn(progress.status, (SEMESTER_PROGRESS_FAILED, SEMESTER_PROGRESS_NOT_STARTED))

    def test_evaluate_locked_raises(self):
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem1, status=SEMESTER_PROGRESS_LOCKED,
        )
        with self.assertRaises(ValueError):
            evaluate_semester_progress(self.student, self.enrollment, self.sem1)

    def test_evaluate_override_allows_locked(self):
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem1, status=SEMESTER_PROGRESS_LOCKED,
        )
        a = self._create_assessment(self.sem1)
        self._create_mcq_question(a)
        self._create_attempt(a)
        progress = evaluate_semester_progress(self.student, self.enrollment, self.sem1, is_override=True)
        self.assertEqual(progress.status, SEMESTER_PROGRESS_PASSED)

    # ------------------------------------------------------------------
    # Manual promotion
    # ------------------------------------------------------------------

    def test_manual_promote(self):
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem1, status=SEMESTER_PROGRESS_PASSED,
        )
        progress = manual_promote(
            self.student, self.enrollment, self.sem1,
            actor=self.admin, reason="Good progress",
        )
        self.assertEqual(progress.status, SEMESTER_PROGRESS_PROMOTED)
        self.assertEqual(PromotionDecision.objects.count(), 1)

    def test_manual_promote_no_reason_when_failed_raises(self):
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem1, status=SEMESTER_PROGRESS_FAILED,
        )
        with self.assertRaises(ValueError):
            manual_promote(self.student, self.enrollment, self.sem1, actor=self.admin)

    def test_manual_promote_locked_raises(self):
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem1, status=SEMESTER_PROGRESS_LOCKED,
        )
        with self.assertRaises(ValueError):
            manual_promote(self.student, self.enrollment, self.sem1, actor=self.admin, reason="override")

    def test_manual_promote_first_semester_no_progress(self):
        """Promoting from first semester succeeds when no progress exists yet."""
        progress = manual_promote(
            self.student, self.enrollment, self.sem1,
            actor=self.admin, reason="Initial promote",
        )
        self.assertEqual(progress.status, SEMESTER_PROGRESS_PROMOTED)

    def test_manual_promote_skip_semester_raises(self):
        """Cannot skip a semester — target must be the current semester."""
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem1, status=SEMESTER_PROGRESS_PASSED,
        )
        with self.assertRaises(ValueError):
            manual_promote(self.student, self.enrollment, self.sem2, actor=self.admin, reason="skip")

    def test_manual_promote_bad_diploma_semester_raises(self):
        """Target semester must belong to the same diploma."""
        other_diploma = Diploma.objects.create(title="Other", total_semesters=1, created_by=self.admin)
        other_sem = Semester.objects.create(diploma=other_diploma, order=1, title="Other Sem")
        with self.assertRaises(ValueError):
            manual_promote(self.student, self.enrollment, other_sem, actor=self.admin)

    # ------------------------------------------------------------------
    # Demotion
    # ------------------------------------------------------------------

    def test_demote(self):
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem2, status=SEMESTER_PROGRESS_PASSED,
        )

        progress = demote_student(
            self.student, self.enrollment, self.sem2,
            actor=self.admin, reason="Needs improvement",
        )
        self.assertIsNotNone(progress)
        self.assertEqual(PromotionDecision.objects.count(), 1)

    def test_demote_below_first_raises(self):
        """Cannot demote the first semester."""
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem1, status=SEMESTER_PROGRESS_PASSED,
        )
        with self.assertRaises(ValueError):
            demote_student(
                self.student, self.enrollment, self.sem1,
                actor=self.admin, reason="test",
            )

    def test_demote_without_reason_raises(self):
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem2, status=SEMESTER_PROGRESS_PASSED,
        )
        with self.assertRaises(ValueError):
            demote_student(self.student, self.enrollment, self.sem2, actor=self.admin)

    def test_demote_skip_semester_raises(self):
        """Cannot demote a semester that is not the student's current one."""
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem2, status=SEMESTER_PROGRESS_PASSED,
        )
        # sem1 is not the current semester (sem2 is)
        with self.assertRaises(ValueError):
            demote_student(
                self.student, self.enrollment, self.sem1,
                actor=self.admin, reason="Skip attempt",
            )

    def test_demote_no_progress_raises(self):
        """Cannot demote a student with no progress records."""
        with self.assertRaises(ValueError):
            demote_student(
                self.student, self.enrollment, self.sem2,
                actor=self.admin, reason="No progress",
            )

    # ------------------------------------------------------------------
    # Override
    # ------------------------------------------------------------------

    def test_override_pass(self):
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem1, status=SEMESTER_PROGRESS_FAILED,
        )
        progress = override_student_progress(
            self.student, self.enrollment, self.sem1,
            SEMESTER_PROGRESS_PASSED, actor=self.admin, reason="Override reason",
        )
        self.assertEqual(progress.status, SEMESTER_PROGRESS_PASSED)
        self.assertEqual(PromotionDecision.objects.count(), 1)

    def test_override_without_reason_raises(self):
        with self.assertRaises(ValueError):
            override_student_progress(
                self.student, self.enrollment, self.sem1,
                SEMESTER_PROGRESS_PASSED,
            )

    # ------------------------------------------------------------------
    # Lock and unlock
    # ------------------------------------------------------------------

    def test_lock(self):
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem1, status=SEMESTER_PROGRESS_PASSED,
        )
        results = lock_student(
            self.student, self.enrollment, actor=self.admin, reason="Academic issue",
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, SEMESTER_PROGRESS_LOCKED)
        self.assertTrue(PromotionDecision.objects.filter(decision_type="lock").exists())

    def test_lock_without_reason_raises(self):
        with self.assertRaises(ValueError):
            lock_student(self.student, self.enrollment)

    def test_unlock(self):
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem1, status=SEMESTER_PROGRESS_LOCKED,
        )
        results = unlock_student(
            self.student, self.enrollment, actor=self.admin, reason="Resolved",
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, SEMESTER_PROGRESS_IN_PROGRESS)

    # ------------------------------------------------------------------
    # Retake
    # ------------------------------------------------------------------

    def test_assign_retake(self):
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem1, status=SEMESTER_PROGRESS_FAILED,
        )
        progress = assign_retake(
            self.student, self.enrollment, self.sem1,
            actor=self.admin, reason="Assign retake",
        )
        self.assertTrue(progress.retake_required)
        self.assertEqual(progress.retake_count, 1)

    def test_assign_retake_max_count(self):
        self.rule.max_retake_count = 1
        self.rule.save()
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem1, status=SEMESTER_PROGRESS_RETAKE_REQUIRED,
            retake_required=True, retake_count=1,
        )
        with self.assertRaises(ValueError):
            assign_retake(
                self.student, self.enrollment, self.sem1,
                actor=self.admin, reason="Another retake",
            )

    def test_clear_retake(self):
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem1, status=SEMESTER_PROGRESS_RETAKE_REQUIRED,
            retake_required=True, retake_count=1,
        )
        progress = clear_retake(
            self.student, self.enrollment, self.sem1,
            actor=self.admin, reason="Passed retake",
        )
        self.assertFalse(progress.retake_required)

    # ------------------------------------------------------------------
    # Final exam eligibility
    # ------------------------------------------------------------------

    def test_not_eligible_before_all_semesters_passed(self):
        eligibility = check_final_exam_eligibility(self.student, self.enrollment)
        self.assertFalse(eligibility.eligible)

    def test_eligible_after_all_semesters_passed(self):
        for sem in [self.sem1, self.sem2]:
            StudentSemesterProgress.objects.create(
                student=self.student, diploma_enrollment=self.enrollment,
                cohort=self.cohort, semester=sem, status=SEMESTER_PROGRESS_PASSED,
            )
        eligibility = check_final_exam_eligibility(self.student, self.enrollment)
        self.assertTrue(eligibility.eligible)

    def test_locked_not_eligible(self):
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem1, status=SEMESTER_PROGRESS_LOCKED,
        )
        eligibility = check_final_exam_eligibility(self.student, self.enrollment)
        self.assertFalse(eligibility.eligible)

    def test_retake_not_eligible(self):
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem1, status=SEMESTER_PROGRESS_PASSED,
        )
        StudentSemesterProgress.objects.create(
            student=self.student, diploma_enrollment=self.enrollment,
            cohort=self.cohort, semester=self.sem2, status=SEMESTER_PROGRESS_PASSED,
            retake_required=True,
        )
        eligibility = check_final_exam_eligibility(self.student, self.enrollment)
        self.assertFalse(eligibility.eligible)

    # ------------------------------------------------------------------
    # Completion and graduation
    # ------------------------------------------------------------------

    def test_mark_requirements_met(self):
        for sem in [self.sem1, self.sem2]:
            StudentSemesterProgress.objects.create(
                student=self.student, diploma_enrollment=self.enrollment,
                cohort=self.cohort, semester=sem, status=SEMESTER_PROGRESS_PASSED,
            )
        completion = mark_requirements_met(self.student, self.enrollment)
        self.assertEqual(completion.status, "completed_requirements")

    def test_completion_blocked_before_all_passed(self):
        with self.assertRaises(ValueError):
            mark_requirements_met(self.student, self.enrollment)

    def test_mark_completed_without_final_exam(self):
        self.rule.require_final_exam = False
        self.rule.save()
        for sem in [self.sem1, self.sem2]:
            StudentSemesterProgress.objects.create(
                student=self.student, diploma_enrollment=self.enrollment,
                cohort=self.cohort, semester=sem, status=SEMESTER_PROGRESS_PASSED,
            )
        completion = mark_completed(self.student, self.enrollment, actor=self.admin, notes="Well done")
        self.assertEqual(completion.status, "completed")

    def test_mark_graduated(self):
        self.rule.require_final_exam = False
        self.rule.save()
        for sem in [self.sem1, self.sem2]:
            StudentSemesterProgress.objects.create(
                student=self.student, diploma_enrollment=self.enrollment,
                cohort=self.cohort, semester=sem, status=SEMESTER_PROGRESS_PASSED,
            )
        completion = mark_graduated(self.student, self.enrollment, actor=self.admin, notes="Graduated")
        self.assertEqual(completion.status, "graduated")
