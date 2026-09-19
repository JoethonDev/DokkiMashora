from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import Cohort, Diploma, DiplomaEnrollment, DiplomaLecture, Semester
from apps.accounts.models import User
from apps.assessments.constants import (
    ASSESSMENT_TYPE_SEMESTER_EXAM,
    ATTEMPT_STATUS_GRADED,
    ATTEMPT_STATUS_IN_PROGRESS,
    ATTEMPT_STATUS_SUBMITTED,
    QUESTION_TYPE_MCQ,
    SUBMISSION_STATUS_FAILED,
    SUBMISSION_STATUS_PENDING,
    SUBMISSION_STATUS_PROCESSED,
)
from apps.assessments.models import (
    Assessment,
    AssessmentAttempt,
    AssessmentAttemptSnapshotAnswer,
    AssessmentLectureSection,
    AssessmentSectionManualQuestion,
    AssessmentSubmission,
    DiplomaLectureChoice,
    DiplomaLectureQuestion,
)
from apps.assessments.services import (
    accept_diploma_submission,
    generate_diploma_assessment_snapshot,
    process_durable_diploma_submission,
)
from apps.students.models import StudentProfile


class DurableSubmissionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="durable-admin@test.com", password="x")
        self.student = User.objects.create_user(email="durable-student@test.com", password="x")
        self.profile = StudentProfile.objects.create(
            user=self.student,
            full_name="Durable Student",
            date_of_birth=date(2000, 1, 1),
            gender="m",
            address="Address",
            phone="01000000811",
            national_id="10000000000811",
            education="university",
        )
        now = timezone.now()
        diploma = Diploma.objects.create(title="Durable Diploma", total_semesters=1, created_by=self.admin)
        cohort = Cohort.objects.create(
            diploma=diploma, name="Durable Cohort", start_date=date(2026, 1, 1), created_by=self.admin,
        )
        semester = Semester.objects.create(diploma=diploma, title="Semester 1", order=1)
        lecture = DiplomaLecture.objects.create(
            cohort=cohort, semester=semester, order=1, title="Lecture 1",
        )
        self.assessment = Assessment.objects.create(
            title="Durable Exam",
            assessment_type=ASSESSMENT_TYPE_SEMESTER_EXAM,
            semester=semester,
            cohort=cohort,
            open_at=now - timedelta(hours=1),
            close_at=now + timedelta(hours=1),
            duration_minutes=60,
            passing_score=Decimal("50.00"),
            total_points=Decimal("10.00"),
            is_active=True,
            created_by=self.admin,
        )
        section = AssessmentLectureSection.objects.create(
            assessment=self.assessment,
            lecture=lecture,
            order=1,
            mcq_count=1,
            max_grade=Decimal("10.00"),
        )
        AssessmentSectionManualQuestion.objects.create(
            section=section,
            text="Question",
            points=Decimal("10.00"),
            order=1,
            required=True,
        )
        bank_question = DiplomaLectureQuestion.objects.create(
            lecture=lecture,
            text="MCQ",
            question_type=QUESTION_TYPE_MCQ,
            points=Decimal("10.00"),
            active=True,
            created_by=self.admin,
        )
        DiplomaLectureChoice.objects.create(
            question=bank_question, text="Correct", is_correct=True, order=1,
        )
        DiplomaLectureChoice.objects.create(
            question=bank_question, text="Wrong", is_correct=False, order=2,
        )
        DiplomaEnrollment.objects.create(
            student=self.profile, cohort=cohort, status=ENROLLMENT_STATUS_ACTIVE,
        )
        self.attempt = AssessmentAttempt.objects.create(
            assessment=self.assessment,
            student=self.student,
            attempt_number=1,
            status=ATTEMPT_STATUS_IN_PROGRESS,
            started_at=now,
        )
        generate_diploma_assessment_snapshot(self.attempt)
        self.mcq = self.attempt.snapshot_questions.get(question_type=QUESTION_TYPE_MCQ)
        self.correct_choice = self.mcq.choices.get(is_correct_snapshot=True)

    @patch("apps.assessments.services._dispatch_diploma_submission")
    def test_accept_closes_attempt_and_persists_final_payload(self, dispatch):
        with self.captureOnCommitCallbacks(execute=True):
            submission = accept_diploma_submission(
                self.attempt,
                "1" * 32,
                {f"q_{self.mcq.order}": str(self.correct_choice.pk)},
            )

        self.assertEqual(submission.processing_status, SUBMISSION_STATUS_PENDING)
        self.assertEqual(submission.final_payload["answers"][f"q_{self.mcq.order}"], str(self.correct_choice.pk))
        self.assertEqual(
            AssessmentAttempt.objects.get(pk=self.attempt.pk).status,
            ATTEMPT_STATUS_SUBMITTED,
        )
        dispatch.assert_called_once_with("1" * 32)

    @patch("apps.assessments.services._dispatch_diploma_submission")
    def test_same_submission_id_is_idempotent(self, dispatch):
        with self.captureOnCommitCallbacks(execute=True):
            first = accept_diploma_submission(self.attempt, "2" * 32, {})
            second = accept_diploma_submission(self.attempt, "2" * 32, {"q_1": "changed"})

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(AssessmentSubmission.objects.filter(attempt=self.attempt).count(), 1)
        dispatch.assert_called_once_with("2" * 32)

    @patch("apps.assessments.services._dispatch_diploma_submission")
    def test_worker_is_idempotent_after_success(self, dispatch):
        submission = accept_diploma_submission(
            self.attempt,
            "3" * 32,
            {f"q_{self.mcq.order}": str(self.correct_choice.pk)},
        )

        process_durable_diploma_submission(submission.submission_id)
        process_durable_diploma_submission(submission.submission_id)

        submission.refresh_from_db()
        self.attempt.refresh_from_db()
        self.assertEqual(submission.processing_status, SUBMISSION_STATUS_PROCESSED)
        self.assertEqual(self.attempt.status, ATTEMPT_STATUS_GRADED)
        self.assertEqual(self.attempt.score_points, Decimal("10.00"))
        self.assertEqual(AssessmentAttemptSnapshotAnswer.objects.filter(attempt_question=self.mcq).count(), 1)

    @patch("apps.assessments.services._dispatch_diploma_submission")
    def test_failed_worker_attempt_is_recoverable(self, dispatch):
        submission = accept_diploma_submission(
            self.attempt,
            "4" * 32,
            {f"q_{self.mcq.order}": str(self.correct_choice.pk)},
        )
        with patch(
            "apps.assessments.services.submit_diploma_assessment",
            side_effect=RuntimeError("temporary database outage"),
        ):
            with self.assertRaises(RuntimeError):
                process_durable_diploma_submission(submission.submission_id)

        submission.refresh_from_db()
        self.assertEqual(submission.processing_status, SUBMISSION_STATUS_FAILED)
        process_durable_diploma_submission(submission.submission_id)
        submission.refresh_from_db()
        self.assertEqual(submission.processing_status, SUBMISSION_STATUS_PROCESSED)
