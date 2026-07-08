"""Tests for the assessments app services."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.test import TestCase
from django.utils import timezone

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import Cohort, Course, CourseEnrollment, CourseVersion, Diploma
from apps.academics.services import assign_moderator_to_students, assign_teacher_to_cohort
from apps.accounts.constants import GROUP_ADMIN, GROUP_MODERATOR, GROUP_TEACHER
from apps.accounts.models import User
from apps.assessments.constants import (
    ASSESSMENT_TYPE_HOMEWORK,
    ASSESSMENT_TYPE_QUIZ,
    ATTEMPT_STATUS_ASSIGNED,
    ATTEMPT_STATUS_CANCELLED,
    ATTEMPT_STATUS_GRADED,
    ATTEMPT_STATUS_IN_PROGRESS,
    ATTEMPT_STATUS_RELEASED,
    ATTEMPT_STATUS_SUBMITTED,
    ATTEMPT_STATUS_UNDER_REVIEW,
    HOMEWORK_STATUS_GRADED,
    HOMEWORK_STATUS_UNDER_REVIEW,
    QUESTION_TYPE_MCQ,
    QUESTION_TYPE_WRITTEN,
    SNAPSHOT_QUESTION_SOURCE_BANK,
    SNAPSHOT_QUESTION_SOURCE_MANUAL,
)
from apps.assessments.models import (
    Assessment,
    AssessmentAnswer,
    AssessmentAttempt,
    AssessmentAttemptSnapshotAnswer,
    AssessmentChoice,
    AssessmentLectureSection,
    AssessmentQuestion,
    AssessmentSectionManualQuestion,
    DiplomaLecture,
    DiplomaLectureChoice,
    DiplomaLectureQuestion,
    HomeworkSubmission,
    Semester,
)
from apps.assessments.services import (
    AssessmentAnswerData,
    autosave_attempt,
    check_assessment_access,
    diploma_autosave_attempt,
    generate_diploma_assessment_snapshot,
    get_grading_queue,
    get_homework_pending_grading,
    get_pending_grading,
    get_student_assessments,
    get_student_attempts,
    grade_diploma_snapshot_answer,
    grade_homework,
    grade_written_answer,
    has_active_enrollment,
    hide_grades,
    release_grades,
    start_assessment,
    submit_assessment,
    submit_diploma_assessment,
    submit_homework,
)
from apps.storage.services import FileValidationError, UploadResult
from apps.students.models import StudentProfile


class HasActiveEnrollmentTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.student = User.objects.create_user(email="student@test.com", password="x")
        now = timezone.now()
        self.course = Course.objects.create(title="C1", created_by=self.admin)
        self.cv = CourseVersion.objects.create(
            course=self.course, label="v1", created_by=self.admin,
        )
        self.assessment = Assessment.objects.create(
            title="Quiz",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            open_at=now - timezone.timedelta(days=1),
            close_at=now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            course_version=self.cv,
            created_by=self.admin,
        )

    def test_no_course_version_returns_true(self):
        self.assessment.course_version = None
        self.assertTrue(has_active_enrollment(self.student, self.assessment))

    def test_no_active_enrollment_returns_false(self):
        self.assertFalse(has_active_enrollment(self.student, self.assessment))

    def test_with_cohort_checks_diploma_enrollment(self):
        diploma = Diploma.objects.create(title="D", total_semesters=2, created_by=self.admin)
        cohort = Cohort.objects.create(diploma=diploma, name="C1", start_date=date(2026, 1, 1), created_by=self.admin)
        self.cv.cohort = cohort
        self.cv.save(update_fields=["cohort"])
        self.assertFalse(has_active_enrollment(self.student, self.assessment))


class CheckAssessmentAccessTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.student = User.objects.create_user(email="student@test.com", password="x")
        self.now = timezone.now()
        self.assessment = Assessment.objects.create(
            title="Quiz",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            open_at=self.now - timezone.timedelta(days=1),
            close_at=self.now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            is_active=True,
            created_by=self.admin,
        )

    def test_access_ok(self):
        check_assessment_access(self.student, self.assessment)

    def test_not_active_raises(self):
        self.assessment.is_active = False
        with self.assertRaises(ValueError, msg="Assessment is not active"):
            check_assessment_access(self.student, self.assessment)

    def test_not_opened_raises(self):
        self.assessment.open_at = self.now + timezone.timedelta(days=1)
        with self.assertRaises(ValueError, msg="Assessment has not opened"):
            check_assessment_access(self.student, self.assessment)

    def test_closed_raises(self):
        self.assessment.close_at = self.now - timezone.timedelta(hours=1)
        with self.assertRaises(ValueError, msg="Assessment has already closed"):
            check_assessment_access(self.student, self.assessment)

    def test_max_attempts_reached_raises(self):
        AssessmentAttempt.objects.create(
            assessment=self.assessment, student=self.student, attempt_number=1,
            status=ATTEMPT_STATUS_SUBMITTED,
        )
        self.assessment.max_attempts = 1
        with self.assertRaises(ValueError, msg="maximum number of attempts"):
            check_assessment_access(self.student, self.assessment)

    def test_cancelled_attempt_not_counted(self):
        AssessmentAttempt.objects.create(
            assessment=self.assessment, student=self.student, attempt_number=1,
            status=ATTEMPT_STATUS_ASSIGNED,
        )
        AssessmentAttempt.objects.filter(
            assessment=self.assessment, student=self.student,
        ).update(status=ATTEMPT_STATUS_CANCELLED)
        self.assessment.max_attempts = 1
        check_assessment_access(self.student, self.assessment)


class StartAssessmentTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.student = User.objects.create_user(email="student@test.com", password="x")
        self.now = timezone.now()
        self.assessment = Assessment.objects.create(
            title="Quiz",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            open_at=self.now - timezone.timedelta(days=1),
            close_at=self.now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            is_active=True,
            max_attempts=2,
            created_by=self.admin,
        )

    def test_start_creates_attempt(self):
        attempt = start_assessment(self.student, self.assessment)
        self.assertEqual(attempt.status, ATTEMPT_STATUS_IN_PROGRESS)
        self.assertEqual(attempt.attempt_number, 1)
        self.assertIsNotNone(attempt.started_at)

    def test_start_returns_existing_in_progress(self):
        a1 = start_assessment(self.student, self.assessment)
        a2 = start_assessment(self.student, self.assessment)
        self.assertEqual(a1.id, a2.id)
        self.assertEqual(a2.attempt_number, 1)

    def test_max_attempts_reached_raises(self):
        a1 = start_assessment(self.student, self.assessment)
        a1.status = ATTEMPT_STATUS_SUBMITTED
        a1.save(update_fields=["status"])
        attempt2 = start_assessment(self.student, self.assessment)
        self.assertEqual(attempt2.attempt_number, 2)
        attempt2.status = ATTEMPT_STATUS_SUBMITTED
        attempt2.save(update_fields=["status"])
        self.assessment.max_attempts = 2
        self.assessment.save(update_fields=["max_attempts"])
        with self.assertRaises(ValueError, msg="maximum number of attempts"):
            start_assessment(self.student, self.assessment)


class SubmitAssessmentTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.student = User.objects.create_user(email="student@test.com", password="x")
        self.now = timezone.now()
        self.assessment = Assessment.objects.create(
            title="Quiz",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            open_at=self.now - timezone.timedelta(days=1),
            close_at=self.now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            total_points=Decimal("30.00"),
            is_active=True,
            created_by=self.admin,
        )
        self.mcq_q = AssessmentQuestion.objects.create(
            assessment=self.assessment, text="MCQ",
            question_type=QUESTION_TYPE_MCQ,
            points=Decimal("10.00"), order=1,
        )
        self.correct_choice = AssessmentChoice.objects.create(
            question=self.mcq_q, text="Correct", is_correct=True, order=1,
        )
        self.wrong_choice = AssessmentChoice.objects.create(
            question=self.mcq_q, text="Wrong", is_correct=False, order=2,
        )
        self.written_q = AssessmentQuestion.objects.create(
            assessment=self.assessment, text="Essay",
            question_type=QUESTION_TYPE_WRITTEN,
            points=Decimal("20.00"), order=2,
        )
        self.mcq_q.required = False
        self.mcq_q.save(update_fields=["required"])
        self.written_q.required = False
        self.written_q.save(update_fields=["required"])
        self.attempt = AssessmentAttempt.objects.create(
            assessment=self.assessment, student=self.student,
            attempt_number=1, status=ATTEMPT_STATUS_IN_PROGRESS,
            started_at=self.now,
        )

    def test_wrong_status_raises(self):
        self.attempt.status = ATTEMPT_STATUS_SUBMITTED
        self.attempt.save(update_fields=["status"])
        with self.assertRaises(ValueError, msg="in_progress status"):
            submit_assessment(self.attempt, [])

    def test_mcq_correct_answer(self):
        submit_assessment(self.attempt, [
            AssessmentAnswerData(question_id=self.mcq_q.id, selected_choice_id=self.correct_choice.id),
        ])
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, ATTEMPT_STATUS_SUBMITTED)
        self.assertEqual(self.attempt.score_points, Decimal("10.00"))

    def test_mcq_wrong_answer(self):
        submit_assessment(self.attempt, [
            AssessmentAnswerData(question_id=self.mcq_q.id, selected_choice_id=self.wrong_choice.id),
        ])
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.score_points, Decimal("0.00"))

    def test_mcq_with_penalty(self):
        self.assessment.penalty_percent = Decimal("25.00")
        submit_assessment(self.attempt, [
            AssessmentAnswerData(question_id=self.mcq_q.id, selected_choice_id=self.wrong_choice.id),
        ])
        self.attempt.refresh_from_db()
        expected = Decimal("10.00") - (Decimal("10.00") * Decimal("0.25"))
        self.assertEqual(self.attempt.score_points, expected)

    def test_written_answer_sets_under_review(self):
        submit_assessment(self.attempt, [
            AssessmentAnswerData(question_id=self.written_q.id, written_text="My essay"),
        ])
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, ATTEMPT_STATUS_UNDER_REVIEW)
        self.assertEqual(self.attempt.score_points, Decimal("0.00"))

    def test_mixed_answers(self):
        submit_assessment(self.attempt, [
            AssessmentAnswerData(question_id=self.mcq_q.id, selected_choice_id=self.correct_choice.id),
            AssessmentAnswerData(question_id=self.written_q.id, written_text="Essay"),
        ])
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, ATTEMPT_STATUS_UNDER_REVIEW)
        self.assertEqual(self.attempt.score_points, Decimal("10.00"))

    def test_mcq_with_written_text_raises(self):
        with self.assertRaises(ValueError, msg="must not carry written text"):
            submit_assessment(self.attempt, [
                AssessmentAnswerData(question_id=self.mcq_q.id, selected_choice_id=self.correct_choice.id, written_text="nope"),
            ])

    def test_written_with_choice_raises(self):
        with self.assertRaises(ValueError, msg="must not carry a selected choice"):
            submit_assessment(self.attempt, [
                AssessmentAnswerData(question_id=self.written_q.id, selected_choice_id=999, written_text="Essay"),
            ])

    def test_assessment_closed_no_late_raises(self):
        self.assessment.close_at = self.now - timezone.timedelta(hours=1)
        self.assessment.allow_late_submission = False
        with self.assertRaises(ValueError, msg="has already closed"):
            submit_assessment(self.attempt, [])

    def test_assessment_closed_with_late_allowed(self):
        self.assessment.close_at = self.now - timezone.timedelta(hours=1)
        self.assessment.allow_late_submission = True
        submit_assessment(self.attempt, [
            AssessmentAnswerData(question_id=self.mcq_q.id, selected_choice_id=self.correct_choice.id),
        ])
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, ATTEMPT_STATUS_SUBMITTED)

    def test_duration_expired_raises(self):
        self.assessment.duration_minutes = 30
        self.attempt.started_at = self.now - timezone.timedelta(minutes=60)
        self.attempt.save(update_fields=["started_at"])
        with self.assertRaises(ValueError, msg="duration has expired"):
            submit_assessment(self.attempt, [
                AssessmentAnswerData(question_id=self.mcq_q.id, selected_choice_id=self.correct_choice.id),
            ])

    def test_missing_required_question_raises(self):
        self.mcq_q.required = True
        self.mcq_q.save(update_fields=["required"])
        self.written_q.required = True
        self.written_q.save(update_fields=["required"])
        with self.assertRaises(ValueError, msg="required questions"):
            submit_assessment(self.attempt, [])


class GradeWrittenAnswerTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.grader = User.objects.create_user(email="grader@test.com", password="x")
        self.student = User.objects.create_user(email="student@test.com", password="x")
        self.now = timezone.now()
        self.assessment = Assessment.objects.create(
            title="Quiz",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            open_at=self.now - timezone.timedelta(days=1),
            close_at=self.now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            is_active=True,
            created_by=self.admin,
        )
        self.question = AssessmentQuestion.objects.create(
            assessment=self.assessment, text="Essay",
            question_type=QUESTION_TYPE_WRITTEN,
            points=Decimal("20.00"), order=1,
        )
        self.attempt = AssessmentAttempt.objects.create(
            assessment=self.assessment, student=self.student,
            attempt_number=1, status=ATTEMPT_STATUS_UNDER_REVIEW,
        )
        self.answer = AssessmentAnswer.objects.create(
            attempt=self.attempt, question=self.question,
            written_text="My essay",
        )

    def test_grade_written_success(self):
        result = grade_written_answer(self.answer.id, Decimal("15.00"), "Good", self.grader)
        self.assertEqual(result.score, Decimal("15.00"))
        self.assertEqual(result.feedback, "Good")
        self.assertEqual(result.graded_by, self.grader)
        self.assertIsNotNone(result.graded_at)

    def test_score_exceeds_points_raises(self):
        with self.assertRaises(ValueError, msg="cannot exceed"):
            grade_written_answer(self.answer.id, Decimal("25.00"), "Too high", self.grader)

    def test_negative_score_raises(self):
        with self.assertRaises(ValueError, msg="cannot be negative"):
            grade_written_answer(self.answer.id, Decimal("-1.00"), "Negative", self.grader)

    def test_all_answers_graded_sets_attempt_graded(self):
        grade_written_answer(self.answer.id, Decimal("15.00"), "Good", self.grader)
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, ATTEMPT_STATUS_GRADED)
        self.assertIsNotNone(self.attempt.graded_at)
        self.assertIsNotNone(self.attempt.is_pass)


class GradeHomeworkTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.grader = User.objects.create_user(email="grader@test.com", password="x")
        self.student = User.objects.create_user(email="student@test.com", password="x")
        now = timezone.now()
        self.assessment = Assessment.objects.create(
            title="Homework",
            assessment_type=ASSESSMENT_TYPE_HOMEWORK,
            open_at=now - timezone.timedelta(days=1),
            close_at=now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            is_active=True,
            created_by=self.admin,
        )
        self.submission = HomeworkSubmission.objects.create(
            assessment=self.assessment, student=self.student,
            original_filename="hw.pdf", storage_key="hw/hw.pdf",
            content_type="application/pdf", file_size=1024,
        )

    def test_grade_homework_success(self):
        result = grade_homework(self.submission.id, Decimal("18.00"), "Great work", self.grader)
        self.assertEqual(result.score, Decimal("18.00"))
        self.assertEqual(result.feedback, "Great work")
        self.assertEqual(result.status, HOMEWORK_STATUS_GRADED)
        self.assertIsNotNone(result.reviewed_at)
        self.assertEqual(result.reviewed_by, self.grader)


class ReleaseAndHideGradesTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.student = User.objects.create_user(email="student@test.com", password="x")
        self.now = timezone.now()
        self.assessment = Assessment.objects.create(
            title="Quiz",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            open_at=self.now - timezone.timedelta(days=1),
            close_at=self.now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            is_active=True,
            created_by=self.admin,
        )
        self.attempt = AssessmentAttempt.objects.create(
            assessment=self.assessment, student=self.student,
            attempt_number=1, status=ATTEMPT_STATUS_GRADED,
            score_points=Decimal("10.00"), score_percent=Decimal("50.00"),
        )

    def test_release_grades(self):
        result = release_grades(self.attempt)
        self.assertEqual(result.status, ATTEMPT_STATUS_RELEASED)
        self.assertTrue(result.visible_to_student)

    def test_release_wrong_status_raises(self):
        self.attempt.status = ATTEMPT_STATUS_SUBMITTED
        self.attempt.save(update_fields=["status"])
        with self.assertRaises(ValueError, msg="graded attempts"):
            release_grades(self.attempt)

    def test_hide_grades(self):
        self.attempt.visible_to_student = True
        self.attempt.status = ATTEMPT_STATUS_RELEASED
        self.attempt.save(update_fields=["visible_to_student", "status"])
        result = hide_grades(self.attempt)
        self.assertFalse(result.visible_to_student)
        self.assertEqual(result.status, ATTEMPT_STATUS_GRADED)


class QueryTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.student = User.objects.create_user(email="student@test.com", password="x")
        self.other_student = User.objects.create_user(email="other-student@test.com", password="x")
        self.student_profile = StudentProfile.objects.create(
            user=self.student,
            full_name="Queue Student",
            date_of_birth=date(2000, 1, 1),
            gender="m",
            address="Address",
            phone="01000000901",
            national_id="10000000000901",
            education="university",
        )
        self.other_profile = StudentProfile.objects.create(
            user=self.other_student,
            full_name="Other Queue Student",
            date_of_birth=date(2000, 1, 1),
            gender="m",
            address="Address",
            phone="01000000902",
            national_id="10000000000902",
            education="university",
        )
        self.now = timezone.now()
        self.diploma = Diploma.objects.create(title="Queue Diploma", total_semesters=2, created_by=self.admin)
        self.cohort = Cohort.objects.create(
            diploma=self.diploma,
            name="Queue 2026",
            start_date=date(2026, 1, 1),
            created_by=self.admin,
        )
        self.course = Course.objects.create(title="Queue Course", created_by=self.admin)
        self.course_version = CourseVersion.objects.create(
            course=self.course,
            cohort=self.cohort,
            label="v1",
            created_by=self.admin,
        )
        self.active_assessment = Assessment.objects.create(
            title="Active Quiz",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            open_at=self.now - timezone.timedelta(days=1),
            close_at=self.now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            is_active=True,
            created_by=self.admin,
        )
        self.scoped_assessment = Assessment.objects.create(
            title="Scoped Quiz",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            course_version=self.course_version,
            open_at=self.now - timezone.timedelta(days=1),
            close_at=self.now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            is_active=True,
            created_by=self.admin,
        )
        self.inactive_assessment = Assessment.objects.create(
            title="Inactive",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            open_at=self.now - timezone.timedelta(days=1),
            close_at=self.now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            is_active=False,
            created_by=self.admin,
        )

    def test_get_student_assessments_only_active(self):
        results = get_student_assessments(self.student)
        self.assertIn(self.active_assessment, results)
        self.assertNotIn(self.inactive_assessment, results)

    def test_get_student_attempts(self):
        AssessmentAttempt.objects.create(
            assessment=self.active_assessment, student=self.student, attempt_number=1,
        )
        results = get_student_attempts(self.student)
        self.assertEqual(len(results), 1)

    def test_get_pending_grading(self):
        AssessmentAttempt.objects.create(
            assessment=self.active_assessment, student=self.student,
            attempt_number=1, status=ATTEMPT_STATUS_UNDER_REVIEW,
        )
        results = get_pending_grading()
        self.assertEqual(len(results), 1)

    def test_get_pending_grading_empty(self):
        results = get_pending_grading()
        self.assertEqual(len(results), 0)

    def test_grading_queue_for_admin(self):
        group, _ = Group.objects.get_or_create(name=GROUP_ADMIN)
        self.admin.groups.add(group)
        AssessmentAttempt.objects.create(
            assessment=self.active_assessment, student=self.student,
            attempt_number=1, status=ATTEMPT_STATUS_UNDER_REVIEW,
        )
        results = get_grading_queue(self.admin)
        self.assertEqual(len(results), 1)

    def test_grading_queue_for_teacher(self):
        group, _ = Group.objects.get_or_create(name=GROUP_TEACHER)
        self.student.groups.add(group)
        results = get_grading_queue(self.student)
        self.assertEqual(len(results), 0)

    def test_teacher_grading_queue_is_limited_to_assigned_cohort(self):
        teacher = User.objects.create_user(email="teacher@test.com", password="x")
        group, _ = Group.objects.get_or_create(name=GROUP_TEACHER)
        teacher.groups.add(group)
        assign_teacher_to_cohort(teacher, self.cohort, self.admin)
        visible = AssessmentAttempt.objects.create(
            assessment=self.scoped_assessment,
            student=self.student,
            attempt_number=1,
            status=ATTEMPT_STATUS_UNDER_REVIEW,
        )
        AssessmentAttempt.objects.create(
            assessment=self.active_assessment,
            student=self.other_student,
            attempt_number=1,
            status=ATTEMPT_STATUS_UNDER_REVIEW,
        )

        results = list(get_grading_queue(teacher))

        self.assertEqual(results, [visible])

    def test_moderator_grading_queue_is_limited_to_assigned_student_and_cohort(self):
        moderator = User.objects.create_user(email="moderator@test.com", password="x")
        group, _ = Group.objects.get_or_create(name=GROUP_MODERATOR)
        moderator.groups.add(group)
        assign_moderator_to_students(moderator, [self.student_profile], self.cohort, self.admin)
        visible = AssessmentAttempt.objects.create(
            assessment=self.scoped_assessment,
            student=self.student,
            attempt_number=1,
            status=ATTEMPT_STATUS_UNDER_REVIEW,
        )
        AssessmentAttempt.objects.create(
            assessment=self.scoped_assessment,
            student=self.other_student,
            attempt_number=1,
            status=ATTEMPT_STATUS_UNDER_REVIEW,
        )

        results = list(get_grading_queue(moderator))

        self.assertEqual(results, [visible])

    def test_grading_queue_for_superuser(self):
        AssessmentAttempt.objects.create(
            assessment=self.active_assessment, student=self.student,
            attempt_number=1, status=ATTEMPT_STATUS_UNDER_REVIEW,
        )
        results = get_grading_queue(self.admin)
        self.assertEqual(len(results), 1)


class StudentAssessmentVisibilityTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.enrolled_user = User.objects.create_user(email="student1@test.com", password="x")
        self.outsider_user = User.objects.create_user(email="student2@test.com", password="x")
        self.enrolled_profile = StudentProfile.objects.create(
            user=self.enrolled_user,
            full_name="Student One",
            date_of_birth=date(2000, 1, 1),
            gender="m",
            address="Address",
            phone="01000000001",
            national_id="10000000000001",
            education="university",
            specialization="Spec",
            employer="Employer",
            current_job="Job",
            church="Church",
            priest_name="Priest",
            priest_contact="01000000002",
        )
        self.outsider_profile = StudentProfile.objects.create(
            user=self.outsider_user,
            full_name="Student Two",
            date_of_birth=date(2000, 1, 1),
            gender="m",
            address="Address",
            phone="01000000003",
            national_id="10000000000002",
            education="university",
            specialization="Spec",
            employer="Employer",
            current_job="Job",
            church="Church",
            priest_name="Priest",
            priest_contact="01000000004",
        )
        self.course = Course.objects.create(title="Course", created_by=self.admin)
        self.course_version = CourseVersion.objects.create(
            course=self.course,
            label="v1",
            created_by=self.admin,
        )
        self.visible_assessment = Assessment.objects.create(
            title="Visible Quiz",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            course_version=self.course_version,
            open_at=timezone.now() - timezone.timedelta(days=1),
            close_at=timezone.now() + timezone.timedelta(days=1),
            passing_score=Decimal("50.00"),
            is_active=True,
            created_by=self.admin,
        )
        self.hidden_assessment = Assessment.objects.create(
            title="Hidden Quiz",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            course_version=self.course_version,
            open_at=timezone.now() - timezone.timedelta(days=1),
            close_at=timezone.now() + timezone.timedelta(days=1),
            passing_score=Decimal("50.00"),
            is_active=True,
            created_by=self.admin,
        )
        CourseEnrollment.objects.create(
            student=self.enrolled_profile,
            course_version=self.course_version,
            status=ENROLLMENT_STATUS_ACTIVE,
        )

    def test_get_student_assessments_filters_by_enrollment(self):
        results = get_student_assessments(self.enrolled_user)
        self.assertIn(self.visible_assessment, results)
        self.assertIn(self.hidden_assessment, results)

        outsider_results = get_student_assessments(self.outsider_user)
        self.assertNotIn(self.visible_assessment, outsider_results)
        self.assertNotIn(self.hidden_assessment, outsider_results)


class SubmitHomeworkTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.student = User.objects.create_user(email="student@test.com", password="x")
        self.now = timezone.now()
        self.assessment = Assessment.objects.create(
            title="Homework",
            assessment_type=ASSESSMENT_TYPE_HOMEWORK,
            open_at=self.now - timezone.timedelta(days=1),
            close_at=self.now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            is_active=True,
            max_attempts=3,
            created_by=self.admin,
        )
        self.file_data = b"fake pdf content"
        self.filename = "essay.pdf"
        self.content_type = "application/pdf"

    @patch("apps.assessments.services.storage_service")
    def test_submit_homework_creates_attempt_and_submission(self, mock_storage):
        mock_storage.upload_file.return_value = UploadResult(
            key="homework/1/abc.pdf",
            content_type="application/pdf",
            byte_size=len(self.file_data),
            checksum="abc123",
            backend="local",
        )
        submission = submit_homework(
            student=self.student,
            assessment=self.assessment,
            file_data=self.file_data,
            original_filename=self.filename,
            content_type=self.content_type,
        )
        self.assertEqual(submission.original_filename, "essay.pdf")
        self.assertEqual(submission.storage_key, "homework/1/abc.pdf")
        self.assertEqual(submission.content_type, "application/pdf")
        self.assertEqual(submission.file_size, len(self.file_data))
        self.assertEqual(submission.checksum, "abc123")
        self.assertEqual(submission.status, HOMEWORK_STATUS_UNDER_REVIEW)
        self.assertIsNotNone(submission.submitted_at)
        self.assertIsNotNone(submission.attempt)
        self.assertEqual(submission.attempt.status, ATTEMPT_STATUS_SUBMITTED)
        self.assertEqual(submission.attempt.student, self.student)

    @patch("apps.assessments.services.storage_service")
    def test_submit_homework_raises_when_inactive(self, mock_storage):
        self.assessment.is_active = False
        self.assessment.save(update_fields=["is_active"])
        with self.assertRaises(ValueError, msg="not active"):
            submit_homework(
                student=self.student, assessment=self.assessment,
                file_data=self.file_data, original_filename=self.filename,
                content_type=self.content_type,
            )
        mock_storage.upload_file.assert_not_called()

    @patch("apps.assessments.services.storage_service")
    def test_submit_homework_blocks_duplicate_submission(self, mock_storage):
        HomeworkSubmission.objects.create(
            assessment=self.assessment,
            student=self.student,
            original_filename="existing.pdf",
            storage_key="homework/1/existing.pdf",
            content_type="application/pdf",
            file_size=100,
            status=HOMEWORK_STATUS_UNDER_REVIEW,
        )
        with self.assertRaises(ValueError, msg="Maximum number of files"):
            submit_homework(
                student=self.student,
                assessment=self.assessment,
                file_data=self.file_data,
                original_filename=self.filename,
                content_type=self.content_type,
            )

    @patch("apps.assessments.services.storage_service")
    def test_submit_homework_respects_max_upload_files(self, mock_storage):
        mock_storage.upload_file.return_value = UploadResult(
            key="homework/1/abc.pdf",
            content_type="application/pdf",
            byte_size=len(self.file_data),
            checksum="abc",
            backend="local",
        )
        self.assessment.max_upload_files = 2
        self.assessment.save()
        submit_homework(self.student, self.assessment, self.file_data, "a.pdf", "application/pdf")
        submit_homework(self.student, self.assessment, self.file_data, "b.pdf", "application/pdf")
        with self.assertRaises(ValueError, msg="Maximum number of files"):
            submit_homework(self.student, self.assessment, self.file_data, "c.pdf", "application/pdf")

    def test_submit_homework_max_attempts(self):
        for i in range(3):
            AssessmentAttempt.objects.create(
                assessment=self.assessment, student=self.student,
                attempt_number=i + 1, status=ATTEMPT_STATUS_SUBMITTED,
            )
        with self.assertRaises(ValueError, msg="maximum number of attempts"):
            submit_homework(
                student=self.student, assessment=self.assessment,
                file_data=self.file_data, original_filename=self.filename,
                content_type=self.content_type,
            )

    def test_submit_homework_rejects_mismatched_mime(self):
        with self.assertRaises(FileValidationError, msg="mime"):
            submit_homework(
                student=self.student,
                assessment=self.assessment,
                file_data=self.file_data,
                original_filename="essay.pdf",
                content_type="text/plain",
            )

    def test_submit_homework_rejects_blocked_extension(self):
        with self.assertRaises(FileValidationError, msg="not allowed"):
            submit_homework(
                student=self.student,
                assessment=self.assessment,
                file_data=self.file_data,
                original_filename="malware.exe",
                content_type="application/x-msdownload",
            )


class GetHomeworkPendingGradingTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.student = User.objects.create_user(email="student@test.com", password="x")
        now = timezone.now()
        self.assessment = Assessment.objects.create(
            title="Homework",
            assessment_type=ASSESSMENT_TYPE_HOMEWORK,
            open_at=now - timezone.timedelta(days=1),
            close_at=now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            is_active=True,
            created_by=self.admin,
        )

    def test_returns_uploaded_submissions(self):
        HomeworkSubmission.objects.create(
            assessment=self.assessment, student=self.student,
            original_filename="hw.pdf", storage_key="k",
            content_type="application/pdf", file_size=100,
            status=HOMEWORK_STATUS_UNDER_REVIEW,
        )
        results = get_homework_pending_grading()
        self.assertEqual(len(results), 1)

    def test_excludes_graded_submissions(self):
        HomeworkSubmission.objects.create(
            assessment=self.assessment, student=self.student,
            original_filename="hw.pdf", storage_key="k",
            content_type="application/pdf", file_size=100,
            status=HOMEWORK_STATUS_GRADED,
        )
        results = get_homework_pending_grading()
        self.assertEqual(len(results), 0)

    def test_empty_when_no_submissions(self):
        results = get_homework_pending_grading()
        self.assertEqual(len(results), 0)


class FullFlowIntegrationTests(TestCase):
    """Integration tests covering the complete student → admin grading lifecycle."""

    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.grader = User.objects.create_user(email="grader@test.com", password="x")
        self.student = User.objects.create_user(email="student@test.com", password="x")
        self.now = timezone.now()
        self.assessment = Assessment.objects.create(
            title="Integration Quiz",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            open_at=self.now - timezone.timedelta(days=1),
            close_at=self.now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            total_points=Decimal("30.00"),
            is_active=True,
            max_attempts=2,
            created_by=self.admin,
        )
        self.mcq_q = AssessmentQuestion.objects.create(
            assessment=self.assessment, text="MCQ Q",
            question_type=QUESTION_TYPE_MCQ, points=Decimal("10.00"), order=1,
        )
        self.correct = AssessmentChoice.objects.create(
            question=self.mcq_q, text="Correct", is_correct=True, order=1,
        )
        self.wrong = AssessmentChoice.objects.create(
            question=self.mcq_q, text="Wrong", is_correct=False, order=2,
        )
        self.written_q = AssessmentQuestion.objects.create(
            assessment=self.assessment, text="Essay Q",
            question_type=QUESTION_TYPE_WRITTEN, points=Decimal("20.00"), order=2,
        )
        self.mcq_q.required = False
        self.mcq_q.save(update_fields=["required"])
        self.written_q.required = False
        self.written_q.save(update_fields=["required"])

    def test_full_quiz_flow(self):
        # 1. Start attempt
        attempt = start_assessment(self.student, self.assessment)
        self.assertEqual(attempt.status, ATTEMPT_STATUS_IN_PROGRESS)

        # 2. Submit with correct MCQ + written answer
        submit_assessment(attempt, [
            AssessmentAnswerData(question_id=self.mcq_q.id, selected_choice_id=self.correct.id),
            AssessmentAnswerData(question_id=self.written_q.id, written_text="My essay answer"),
        ])
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, ATTEMPT_STATUS_UNDER_REVIEW)
        self.assertEqual(attempt.score_points, Decimal("10.00"))

        # 3. Grader grades written answer
        written_answer = AssessmentAnswer.objects.get(question=self.written_q, attempt=attempt)
        grade_written_answer(written_answer.id, Decimal("15.00"), "Good work", self.grader)
        written_answer.refresh_from_db()
        self.assertEqual(written_answer.score, Decimal("15.00"))

        # 4. Attempt moves to graded
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, ATTEMPT_STATUS_GRADED)
        self.assertEqual(attempt.score_points, Decimal("25.00"))

        # 5. Admin releases grade
        release_grades(attempt)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, ATTEMPT_STATUS_RELEASED)
        self.assertTrue(attempt.visible_to_student)

        # 6. Verify pending queue is empty
        queue = get_pending_grading()
        self.assertEqual(len(queue), 0)

    def test_override_written_answer_requires_reason(self):
        attempt = start_assessment(self.student, self.assessment)
        submit_assessment(attempt, [
            AssessmentAnswerData(question_id=self.written_q.id, written_text="Essay"),
        ])
        written_answer = AssessmentAnswer.objects.get(question=self.written_q, attempt=attempt)
        grade_written_answer(written_answer.id, Decimal("15.00"), "Good", self.grader)

        with self.assertRaises(ValueError, msg="override reason is required"):
            grade_written_answer(written_answer.id, Decimal("18.00"), "Updated", self.grader)

    def test_override_written_answer_with_reason(self):
        attempt = start_assessment(self.student, self.assessment)
        submit_assessment(attempt, [
            AssessmentAnswerData(question_id=self.written_q.id, written_text="Essay"),
        ])
        written_answer = AssessmentAnswer.objects.get(question=self.written_q, attempt=attempt)
        grade_written_answer(written_answer.id, Decimal("15.00"), "Good", self.grader)

        result = grade_written_answer(
            written_answer.id, Decimal("18.00"), "Updated feedback", self.grader,
            override_reason="Re-evaluated rubric",
        )
        self.assertEqual(result.score, Decimal("18.00"))
        self.assertEqual(result.score_override_reason, "Re-evaluated rubric")

    def test_full_homework_flow(self):
        hw_assessment = Assessment.objects.create(
            title="Integration Homework",
            assessment_type=ASSESSMENT_TYPE_HOMEWORK,
            open_at=self.now - timezone.timedelta(days=1),
            close_at=self.now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            is_active=True,
            max_attempts=3,
            created_by=self.admin,
        )
        with patch("apps.assessments.services.storage_service") as mock_storage:
            mock_storage.upload_file.return_value = UploadResult(
                key="hw/test.pdf", content_type="application/pdf",
                byte_size=100, checksum="abc", backend="local",
            )
            submission = submit_homework(
                student=self.student, assessment=hw_assessment,
                file_data=b"pdf content", original_filename="hw.pdf",
                content_type="application/pdf",
            )
        self.assertEqual(submission.status, HOMEWORK_STATUS_UNDER_REVIEW)

        grade_homework(submission.id, Decimal("18.00"), "Great", self.grader)
        submission.refresh_from_db()
        self.assertEqual(submission.status, HOMEWORK_STATUS_GRADED)
        self.assertEqual(submission.score, Decimal("18.00"))
        self.assertEqual(submission.feedback, "Great")

    def test_penalty_flow(self):
        self.assessment.penalty_percent = Decimal("25.00")
        self.assessment.save(update_fields=["penalty_percent"])
        attempt = start_assessment(self.student, self.assessment)
        submit_assessment(attempt, [
            AssessmentAnswerData(question_id=self.mcq_q.id, selected_choice_id=self.wrong.id),
        ])
        attempt.refresh_from_db()
        expected = Decimal("10.00") - (Decimal("10.00") * Decimal("0.25"))
        self.assertEqual(attempt.score_points, expected)

    def test_mixed_assessment_with_ungraded_keeps_under_review(self):
        attempt = start_assessment(self.student, self.assessment)
        submit_assessment(attempt, [
            AssessmentAnswerData(question_id=self.mcq_q.id, selected_choice_id=self.correct.id),
            AssessmentAnswerData(question_id=self.written_q.id, written_text="Essay"),
        ])
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, ATTEMPT_STATUS_UNDER_REVIEW)
        self.assertEqual(attempt.score_points, Decimal("10.00"))
        self.assertIsNotNone(attempt.score_percent)


class GradingQueueQueryCountTests(TestCase):
    """Verify grading queue selectors produce bounded queries."""

    def setUp(self) -> None:
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.student = User.objects.create_user(email="student@test.com", password="x")
        self.assessment = Assessment.objects.create(
            title="Quiz", assessment_type=ASSESSMENT_TYPE_QUIZ,
            created_by=self.admin,
            open_at=timezone.now() - timezone.timedelta(hours=1),
            close_at=timezone.now() + timezone.timedelta(hours=2),
            passing_score=Decimal("50.00"),
        )
        for i in range(5):
            u = User.objects.create_user(email=f"s{i}@test.com", password="x")
            AssessmentAttempt.objects.create(
                assessment=self.assessment, student=u,
                attempt_number=1, status=ATTEMPT_STATUS_UNDER_REVIEW,
            )

    def test_get_pending_grading_query_count_bounded(self) -> None:
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as captured:
            list(get_pending_grading())
        self.assertLess(len(captured.captured_queries), 5,
                        f"get_pending_grading used {len(captured.captured_queries)} queries; expected < 5")

    def test_get_homework_pending_grading_query_count_bounded(self) -> None:
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as captured:
            list(get_homework_pending_grading())
        self.assertLess(len(captured.captured_queries), 5,
                        f"get_homework_pending_grading used {len(captured.captured_queries)} queries; expected < 5")

    def test_get_grading_queue_query_count_bounded(self) -> None:
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as captured:
            list(get_grading_queue(self.admin))
        self.assertLess(len(captured.captured_queries), 10,
                        f"get_grading_queue used {len(captured.captured_queries)} queries; expected < 10")


class AutosaveAttemptTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.student = User.objects.create_user(email="student@test.com", password="x")
        self.now = timezone.now()
        self.assessment = Assessment.objects.create(
            title="Quiz",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            open_at=self.now - timezone.timedelta(days=1),
            close_at=self.now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            total_points=Decimal("30.00"),
            is_active=True,
            created_by=self.admin,
        )
        self.mcq_q = AssessmentQuestion.objects.create(
            assessment=self.assessment, text="MCQ Q",
            question_type=QUESTION_TYPE_MCQ, points=Decimal("10.00"), order=1,
        )
        self.choice = AssessmentChoice.objects.create(
            question=self.mcq_q, text="A", is_correct=True, order=1,
        )
        self.written_q = AssessmentQuestion.objects.create(
            assessment=self.assessment, text="Essay",
            question_type=QUESTION_TYPE_WRITTEN, points=Decimal("20.00"), order=2,
        )
        self.attempt = AssessmentAttempt.objects.create(
            assessment=self.assessment, student=self.student,
            attempt_number=1, status=ATTEMPT_STATUS_IN_PROGRESS,
            started_at=self.now,
        )

    def test_autosave_creates_mcq_answer(self):
        autosave_attempt(self.attempt, [
            AssessmentAnswerData(question_id=self.mcq_q.id, selected_choice_id=self.choice.id),
        ])
        answer = AssessmentAnswer.objects.get(attempt=self.attempt, question=self.mcq_q)
        self.assertEqual(answer.selected_choice_id, self.choice.id)
        self.assertEqual(answer.written_text, "")

    def test_autosave_creates_written_answer(self):
        autosave_attempt(self.attempt, [
            AssessmentAnswerData(question_id=self.written_q.id, written_text="Draft"),
        ])
        answer = AssessmentAnswer.objects.get(attempt=self.attempt, question=self.written_q)
        self.assertEqual(answer.written_text, "Draft")

    def test_autosave_does_not_score(self):
        autosave_attempt(self.attempt, [
            AssessmentAnswerData(question_id=self.mcq_q.id, selected_choice_id=self.choice.id),
        ])
        answer = AssessmentAnswer.objects.get(attempt=self.attempt, question=self.mcq_q)
        self.assertIsNone(answer.score)

    def test_autosave_does_not_change_status(self):
        autosave_attempt(self.attempt, [
            AssessmentAnswerData(question_id=self.mcq_q.id, selected_choice_id=self.choice.id),
        ])
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, ATTEMPT_STATUS_IN_PROGRESS)

    def test_autosave_noop_on_submitted_attempt(self):
        self.attempt.status = ATTEMPT_STATUS_SUBMITTED
        self.attempt.save(update_fields=["status"])
        autosave_attempt(self.attempt, [
            AssessmentAnswerData(question_id=self.mcq_q.id, selected_choice_id=self.choice.id),
        ])
        self.assertFalse(AssessmentAnswer.objects.filter(attempt=self.attempt).exists())

    def test_autosave_updates_existing_answer(self):
        AssessmentAnswer.objects.create(
            attempt=self.attempt, question=self.mcq_q,
            selected_choice=self.choice, written_text="",
            score=Decimal("10.00"),
        )
        other_choice = AssessmentChoice.objects.create(
            question=self.mcq_q, text="B", is_correct=False, order=2,
        )
        autosave_attempt(self.attempt, [
            AssessmentAnswerData(question_id=self.mcq_q.id, selected_choice_id=other_choice.id),
        ])
        answer = AssessmentAnswer.objects.get(attempt=self.attempt, question=self.mcq_q)
        self.assertEqual(answer.selected_choice_id, other_choice.id)
        self.assertEqual(answer.score, Decimal("10.00"))


class DiplomaSnapshotGenerationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.student = User.objects.create_user(email="student@test.com", password="x")
        self.now = timezone.now()
        self.diploma = Diploma.objects.create(title="D", total_semesters=2, created_by=self.admin)
        self.cohort = Cohort.objects.create(diploma=self.diploma, name="C1", start_date=date(2026, 1, 1), created_by=self.admin)
        self.semester = Semester.objects.create(diploma=self.diploma, title="Sem1", order=1)
        self.lecture = DiplomaLecture.objects.create(cohort=self.cohort, semester=self.semester, order=1, title="L1")
        self.assessment = Assessment.objects.create(
            title="Diploma Quiz",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            semester=self.semester, cohort=self.cohort,
            open_at=self.now - timezone.timedelta(days=1),
            close_at=self.now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"), total_points=Decimal("30.00"),
            is_active=True, created_by=self.admin,
        )
        self.section = AssessmentLectureSection.objects.create(
            assessment=self.assessment, lecture=self.lecture, order=1, mcq_count=2, max_grade=Decimal("30.00"),
        )
        self.mq = AssessmentSectionManualQuestion.objects.create(
            section=self.section, text="Written?", points=Decimal("10.00"), order=1, required=True,
        )
        self.bank_q1 = DiplomaLectureQuestion.objects.create(
            lecture=self.lecture, text="MCQ1", question_type=QUESTION_TYPE_MCQ, points=Decimal("10.00"), active=True, created_by=self.admin,
        )
        DiplomaLectureChoice.objects.create(question=self.bank_q1, text="A", is_correct=True, order=1)
        DiplomaLectureChoice.objects.create(question=self.bank_q1, text="B", is_correct=False, order=2)
        self.bank_q2 = DiplomaLectureQuestion.objects.create(
            lecture=self.lecture, text="MCQ2", question_type=QUESTION_TYPE_MCQ, points=Decimal("10.00"), active=True, created_by=self.admin,
        )
        DiplomaLectureChoice.objects.create(question=self.bank_q2, text="C", is_correct=True, order=1)
        DiplomaLectureChoice.objects.create(question=self.bank_q2, text="D", is_correct=False, order=2)
        self.attempt = AssessmentAttempt.objects.create(
            assessment=self.assessment, student=self.student,
            attempt_number=1, status=ATTEMPT_STATUS_IN_PROGRESS, started_at=self.now,
        )

    def test_generates_correct_number_of_questions(self):
        generate_diploma_assessment_snapshot(self.attempt)
        self.assertEqual(self.attempt.snapshot_questions.count(), 3)  # 1 manual + 2 MCQ
        manual_qs = self.attempt.snapshot_questions.filter(source_type=SNAPSHOT_QUESTION_SOURCE_MANUAL)
        self.assertEqual(manual_qs.count(), 1)
        bank_qs = self.attempt.snapshot_questions.filter(source_type=SNAPSHOT_QUESTION_SOURCE_BANK)
        self.assertEqual(bank_qs.count(), 2)

    def test_generates_choices_for_bank_questions(self):
        generate_diploma_assessment_snapshot(self.attempt)
        for sq in self.attempt.snapshot_questions.filter(source_type=SNAPSHOT_QUESTION_SOURCE_BANK):
            self.assertEqual(sq.choices.count(), 2)
            self.assertTrue(sq.choices.filter(is_correct_snapshot=True).exists())

    def test_idempotent_second_call_does_nothing(self):
        generate_diploma_assessment_snapshot(self.attempt)
        count = self.attempt.snapshot_questions.count()
        generate_diploma_assessment_snapshot(self.attempt)
        self.assertEqual(self.attempt.snapshot_questions.count(), count)

    def test_insufficient_mcqs_raises_error(self):
        self.bank_q2.delete()
        with self.assertRaises(ValueError, msg="needs 2 MCQs"):
            generate_diploma_assessment_snapshot(self.attempt)

    def test_snapshot_text_matches_source(self):
        generate_diploma_assessment_snapshot(self.attempt)
        manual_sq = self.attempt.snapshot_questions.get(source_type=SNAPSHOT_QUESTION_SOURCE_MANUAL)
        self.assertEqual(manual_sq.text_snapshot, self.mq.text)
        bank_texts = self.attempt.snapshot_questions.filter(
            source_type=SNAPSHOT_QUESTION_SOURCE_BANK,
        ).values_list("text_snapshot", flat=True)
        self.assertIn(self.bank_q1.text, list(bank_texts))

    def test_snapshot_answers_created(self):
        generate_diploma_assessment_snapshot(self.attempt)
        for sq in self.attempt.snapshot_questions.all():
            answer = AssessmentAttemptSnapshotAnswer.objects.filter(attempt_question=sq).first()
            self.assertIsNotNone(answer)
            self.assertIsNone(answer.score)

    def test_no_sections_raises(self):
        self.section.delete()
        with self.assertRaises(ValueError, msg="no lecture sections"):
            generate_diploma_assessment_snapshot(self.attempt)


class DiplomaSubmitAssessmentTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.student = User.objects.create_user(email="student@test.com", password="x")
        self.now = timezone.now()
        self.diploma = Diploma.objects.create(title="D", total_semesters=2, created_by=self.admin)
        self.cohort = Cohort.objects.create(diploma=self.diploma, name="C1", start_date=date(2026, 1, 1), created_by=self.admin)
        self.semester = Semester.objects.create(diploma=self.diploma, title="Sem1", order=1)
        self.lecture = DiplomaLecture.objects.create(cohort=self.cohort, semester=self.semester, order=1, title="L1")
        self.assessment = Assessment.objects.create(
            title="Diploma Quiz", assessment_type=ASSESSMENT_TYPE_QUIZ,
            semester=self.semester, cohort=self.cohort,
            open_at=self.now - timezone.timedelta(days=1),
            close_at=self.now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"), total_points=Decimal("20.00"),
            is_active=True, created_by=self.admin,
        )
        self.section = AssessmentLectureSection.objects.create(
            assessment=self.assessment, lecture=self.lecture, order=1, mcq_count=1, max_grade=Decimal("20.00"),
        )
        self.bank_q = DiplomaLectureQuestion.objects.create(
            lecture=self.lecture, text="MCQ", question_type=QUESTION_TYPE_MCQ, points=Decimal("10.00"), active=True, created_by=self.admin,
        )
        DiplomaLectureChoice.objects.create(question=self.bank_q, text="Right", is_correct=True, order=1)
        DiplomaLectureChoice.objects.create(question=self.bank_q, text="Wrong", is_correct=False, order=2)
        self.written_mq = AssessmentSectionManualQuestion.objects.create(
            section=self.section, text="Essay", points=Decimal("10.00"), order=1, required=False,
        )
        self.attempt = AssessmentAttempt.objects.create(
            assessment=self.assessment, student=self.student,
            attempt_number=1, status=ATTEMPT_STATUS_IN_PROGRESS, started_at=self.now,
        )
        generate_diploma_assessment_snapshot(self.attempt)
        self.snapshot_questions = list(self.attempt.snapshot_questions.order_by("order"))
        # Manual question first (order 1), then MCQ (order 2)
        self.written_sq = self.snapshot_questions[0]
        self.mcq_sq = self.snapshot_questions[1]
        # Snapshot choice IDs differ from bank choice IDs
        self.correct_snapshot_choice = self.mcq_sq.choices.get(is_correct_snapshot=True)
        self.wrong_snapshot_choice = self.mcq_sq.choices.get(is_correct_snapshot=False)

    def test_submit_mcq_correct(self):
        submit_diploma_assessment(self.attempt, {f"q_{self.mcq_sq.order}": str(self.correct_snapshot_choice.pk)})
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, ATTEMPT_STATUS_GRADED)
        self.assertEqual(self.attempt.score_points, Decimal("10.00"))

    def test_submit_mcq_wrong(self):
        submit_diploma_assessment(self.attempt, {f"q_{self.mcq_sq.order}": str(self.wrong_snapshot_choice.pk)})
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.score_points, Decimal("0.00"))

    def test_submit_with_written_sets_under_review(self):
        submit_diploma_assessment(self.attempt, {
            f"q_{self.mcq_sq.order}": str(self.correct_snapshot_choice.pk),
            f"q_{self.written_sq.order}": "My essay",
        })
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, ATTEMPT_STATUS_UNDER_REVIEW)
        self.assertEqual(self.attempt.score_points, Decimal("10.00"))

    def test_submit_invalid_choice_raises(self):
        with self.assertRaises(ValueError):
            submit_diploma_assessment(self.attempt, {f"q_{self.mcq_sq.order}": "999"})

    def test_submit_not_in_progress_raises(self):
        self.attempt.status = ATTEMPT_STATUS_SUBMITTED
        self.attempt.save(update_fields=["status"])
        with self.assertRaises(ValueError):
            submit_diploma_assessment(self.attempt, {})

    def test_course_assessment_submit_unaffected(self):
        """Verify course assessment submit still works with existing code path."""
        course = Course.objects.create(title="C1", created_by=self.admin)
        cv = CourseVersion.objects.create(course=course, label="v1", created_by=self.admin)
        course_assessment = Assessment.objects.create(
            title="Course Quiz", assessment_type=ASSESSMENT_TYPE_QUIZ,
            course_version=cv,
            open_at=self.now - timezone.timedelta(days=1),
            close_at=self.now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"), total_points=Decimal("10.00"),
            is_active=True, created_by=self.admin,
        )
        q = AssessmentQuestion.objects.create(
            assessment=course_assessment, text="Q", question_type=QUESTION_TYPE_MCQ, points=Decimal("10.00"), order=1,
        )
        c = AssessmentChoice.objects.create(question=q, text="Right", is_correct=True, order=1)
        attempt = AssessmentAttempt.objects.create(
            assessment=course_assessment, student=self.student,
            attempt_number=1, status=ATTEMPT_STATUS_IN_PROGRESS, started_at=self.now,
        )
        submit_assessment(attempt, [AssessmentAnswerData(question_id=q.id, selected_choice_id=c.id)])
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, ATTEMPT_STATUS_SUBMITTED)
        self.assertEqual(attempt.score_points, Decimal("10.00"))


class DiplomaAutosaveTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.student = User.objects.create_user(email="student@test.com", password="x")
        self.now = timezone.now()
        self.diploma = Diploma.objects.create(title="D", total_semesters=2, created_by=self.admin)
        self.cohort = Cohort.objects.create(diploma=self.diploma, name="C1", start_date=date(2026, 1, 1), created_by=self.admin)
        self.semester = Semester.objects.create(diploma=self.diploma, title="Sem1", order=1)
        self.lecture = DiplomaLecture.objects.create(cohort=self.cohort, semester=self.semester, order=1, title="L1")
        self.assessment = Assessment.objects.create(
            title="Diploma Quiz", assessment_type=ASSESSMENT_TYPE_QUIZ,
            semester=self.semester, cohort=self.cohort,
            open_at=self.now - timezone.timedelta(days=1),
            close_at=self.now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"), total_points=Decimal("20.00"),
            is_active=True, created_by=self.admin,
        )
        self.section = AssessmentLectureSection.objects.create(
            assessment=self.assessment, lecture=self.lecture, order=1, mcq_count=1, max_grade=Decimal("20.00"),
        )
        self.bank_q = DiplomaLectureQuestion.objects.create(
            lecture=self.lecture, text="MCQ", question_type=QUESTION_TYPE_MCQ, points=Decimal("10.00"), active=True, created_by=self.admin,
        )
        DiplomaLectureChoice.objects.create(question=self.bank_q, text="A", is_correct=True, order=1)
        self.written_mq = AssessmentSectionManualQuestion.objects.create(
            section=self.section, text="Essay", points=Decimal("10.00"), order=1, required=False,
        )
        self.attempt = AssessmentAttempt.objects.create(
            assessment=self.assessment, student=self.student,
            attempt_number=1, status=ATTEMPT_STATUS_IN_PROGRESS, started_at=self.now,
        )
        generate_diploma_assessment_snapshot(self.attempt)
        self.snapshot_questions = list(self.attempt.snapshot_questions.order_by("order"))
        self.written_sq = self.snapshot_questions[0]
        self.mcq_sq = self.snapshot_questions[1]
        self.snapshot_choice = self.mcq_sq.choices.first()

    def test_autosave_stores_mcq(self):
        diploma_autosave_attempt(self.attempt, {f"q_{self.mcq_sq.order}": str(self.snapshot_choice.pk)})
        answer = AssessmentAttemptSnapshotAnswer.objects.get(attempt_question=self.mcq_sq)
        self.assertEqual(answer.selected_choice_id, self.snapshot_choice.pk)

    def test_autosave_stores_written(self):
        diploma_autosave_attempt(self.attempt, {f"q_{self.written_sq.order}": "Draft"})
        answer = AssessmentAttemptSnapshotAnswer.objects.get(attempt_question=self.written_sq)
        self.assertEqual(answer.written_text, "Draft")

    def test_autosave_does_not_score(self):
        diploma_autosave_attempt(self.attempt, {f"q_{self.mcq_sq.order}": str(self.snapshot_choice.pk)})
        answer = AssessmentAttemptSnapshotAnswer.objects.get(attempt_question=self.mcq_sq)
        self.assertIsNone(answer.score)

    def test_autosave_does_not_change_status(self):
        diploma_autosave_attempt(self.attempt, {f"q_{self.mcq_sq.order}": str(self.snapshot_choice.pk)})
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, ATTEMPT_STATUS_IN_PROGRESS)


class DiplomaGradingTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.grader = User.objects.create_user(email="grader@test.com", password="x")
        self.student = User.objects.create_user(email="student@test.com", password="x")
        self.now = timezone.now()
        self.diploma = Diploma.objects.create(title="D", total_semesters=2, created_by=self.admin)
        self.cohort = Cohort.objects.create(diploma=self.diploma, name="C1", start_date=date(2026, 1, 1), created_by=self.admin)
        self.semester = Semester.objects.create(diploma=self.diploma, title="Sem1", order=1)
        self.lecture = DiplomaLecture.objects.create(cohort=self.cohort, semester=self.semester, order=1, title="L1")
        self.assessment = Assessment.objects.create(
            title="Diploma Quiz", assessment_type=ASSESSMENT_TYPE_QUIZ,
            semester=self.semester, cohort=self.cohort,
            open_at=self.now - timezone.timedelta(days=1),
            close_at=self.now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"), total_points=Decimal("20.00"),
            is_active=True, created_by=self.admin,
        )
        self.section = AssessmentLectureSection.objects.create(
            assessment=self.assessment, lecture=self.lecture, order=1, mcq_count=0, max_grade=Decimal("20.00"),
        )
        self.written_mq = AssessmentSectionManualQuestion.objects.create(
            section=self.section, text="Essay", points=Decimal("20.00"), order=1, required=True,
        )
        self.attempt = AssessmentAttempt.objects.create(
            assessment=self.assessment, student=self.student,
            attempt_number=1, status=ATTEMPT_STATUS_UNDER_REVIEW, started_at=self.now,
        )
        generate_diploma_assessment_snapshot(self.attempt)
        self.written_sq = self.attempt.snapshot_questions.first()
        self.answer, _ = AssessmentAttemptSnapshotAnswer.objects.get_or_create(attempt_question=self.written_sq)

    def test_grade_snapshot_written_success(self):
        result = grade_diploma_snapshot_answer(self.answer.pk, Decimal("15.00"), "Good", self.grader)
        self.assertEqual(result.score, Decimal("15.00"))
        self.assertEqual(result.feedback, "Good")

    def test_grade_exceeds_points_raises(self):
        with self.assertRaises(ValueError, msg="cannot exceed"):
            grade_diploma_snapshot_answer(self.answer.pk, Decimal("25.00"), "Too high", self.grader)

    def test_negative_score_raises(self):
        with self.assertRaises(ValueError, msg="cannot be negative"):
            grade_diploma_snapshot_answer(self.answer.pk, Decimal("-1.00"), "Negative", self.grader)

    def test_all_questions_graded_sets_attempt_graded(self):
        grade_diploma_snapshot_answer(self.answer.pk, Decimal("15.00"), "Good", self.grader)
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, ATTEMPT_STATUS_GRADED)
        self.assertIsNotNone(self.attempt.graded_at)
        self.assertIn(self.attempt.is_pass, (True, False))

    def test_override_requires_reason(self):
        grade_diploma_snapshot_answer(self.answer.pk, Decimal("15.00"), "Good", self.grader)
        with self.assertRaises(ValueError, msg="override reason is required"):
            grade_diploma_snapshot_answer(self.answer.pk, Decimal("18.00"), "Updated", self.grader)

    def test_override_with_reason(self):
        grade_diploma_snapshot_answer(self.answer.pk, Decimal("15.00"), "Good", self.grader)
        result = grade_diploma_snapshot_answer(
            self.answer.pk, Decimal("18.00"), "Updated", self.grader, override_reason="Re-evaluated",
        )
        self.assertEqual(result.score, Decimal("18.00"))
        self.assertEqual(result.score_override_reason, "Re-evaluated")
