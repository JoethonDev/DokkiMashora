"""Tests for the assessments app models."""

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.assessments.constants import (
    ASSESSMENT_TYPE_QUIZ,
    ATTEMPT_STATUS_ASSIGNED,
    GRADE_VISIBILITY_HIDDEN,
    HOMEWORK_STATUS_UPLOADED,
    QUESTION_TYPE_MCQ,
    QUESTION_TYPE_WRITTEN,
)
from apps.assessments.models import (
    Assessment,
    AssessmentAnswer,
    AssessmentAttempt,
    AssessmentChoice,
    AssessmentQuestion,
    DiplomaLectureChoice,
    DiplomaLectureQuestion,
    HomeworkSubmission,
)
from apps.academics.models import Cohort, Diploma, DiplomaLecture, Semester


class AssessmentTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        now = timezone.now()
        self.assessment = Assessment.objects.create(
            title="Quiz 1",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            open_at=now - timezone.timedelta(days=1),
            close_at=now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            created_by=self.admin,
        )

    def test_str(self):
        self.assertEqual(str(self.assessment), "Quiz 1")

    def test_default_grade_visibility_hidden(self):
        self.assertEqual(self.assessment.grade_visibility, GRADE_VISIBILITY_HIDDEN)

    def test_default_is_active_false(self):
        self.assertFalse(self.assessment.is_active)

    def test_default_max_attempts_one(self):
        self.assertEqual(self.assessment.max_attempts, 1)

    def test_clean_open_before_close(self):
        assessment = Assessment(
            title="Bad",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            open_at=timezone.now(),
            close_at=timezone.now() - timezone.timedelta(hours=1),
            passing_score=Decimal("50.00"),
            created_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            assessment.clean()

    def test_clean_duration_positive(self):
        assessment = Assessment(
            title="Bad",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            open_at=timezone.now() - timezone.timedelta(days=1),
            close_at=timezone.now() + timezone.timedelta(days=1),
            duration_minutes=0,
            passing_score=Decimal("50.00"),
            created_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            assessment.clean()

    def test_clean_max_attempts_at_least_one(self):
        assessment = Assessment(
            title="Bad",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            open_at=timezone.now() - timezone.timedelta(days=1),
            close_at=timezone.now() + timezone.timedelta(days=1),
            max_attempts=0,
            passing_score=Decimal("50.00"),
            created_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            assessment.clean()

    def test_ordering(self):
        now = timezone.now()
        a2 = Assessment.objects.create(
            title="Quiz 2",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            open_at=now - timezone.timedelta(days=2),
            close_at=now + timezone.timedelta(days=5),
            passing_score=Decimal("50.00"),
            created_by=self.admin,
        )
        assessments = list(Assessment.objects.all())
        self.assertEqual(assessments[0], a2)
        self.assertEqual(assessments[1], self.assessment)


class AssessmentQuestionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        now = timezone.now()
        self.assessment = Assessment.objects.create(
            title="Quiz 1",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            open_at=now - timezone.timedelta(days=1),
            close_at=now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            created_by=self.admin,
        )
        self.question = AssessmentQuestion.objects.create(
            assessment=self.assessment,
            text="What is 2+2?",
            question_type=QUESTION_TYPE_MCQ,
            points=Decimal("10.00"),
            order=1,
        )

    def test_str(self):
        self.assertIn("What is 2+2?", str(self.question))

    def test_default_required(self):
        self.assertTrue(self.question.required)

    def test_ordering(self):
        AssessmentQuestion.objects.create(
            assessment=self.assessment,
            text="Second?",
            question_type=QUESTION_TYPE_WRITTEN,
            points=Decimal("5.00"),
            order=2,
        )
        questions = list(self.assessment.questions.all())
        self.assertEqual(questions[0].order, 1)
        self.assertEqual(questions[1].order, 2)

    def test_unique_order_per_assessment(self):
        with self.assertRaises(IntegrityError):
            AssessmentQuestion.objects.create(
                assessment=self.assessment,
                text="Duplicate",
                question_type=QUESTION_TYPE_MCQ,
                points=Decimal("5.00"),
                order=1,
            )

    def test_clean_non_mcq_deletes_choices(self):
        AssessmentChoice.objects.create(
            question=self.question, text="4", is_correct=True, order=1,
        )
        self.assertEqual(self.question.choices.count(), 1)
        self.question.question_type = QUESTION_TYPE_WRITTEN
        self.question.clean()
        self.assertEqual(self.question.choices.count(), 0)


class AssessmentChoiceTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        now = timezone.now()
        assessment = Assessment.objects.create(
            title="Quiz",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            open_at=now - timezone.timedelta(days=1),
            close_at=now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            created_by=self.admin,
        )
        self.question = AssessmentQuestion.objects.create(
            assessment=assessment,
            text="Q?",
            question_type=QUESTION_TYPE_MCQ,
            points=Decimal("10.00"),
            order=1,
        )
        self.choice = AssessmentChoice.objects.create(
            question=self.question, text="Correct", is_correct=True, order=1,
        )

    def test_str(self):
        self.assertIn("Correct", str(self.choice))

    def test_default_not_correct(self):
        c2 = AssessmentChoice.objects.create(
            question=self.question, text="Wrong", order=2,
        )
        self.assertFalse(c2.is_correct)

    def test_unique_order_per_question(self):
        with self.assertRaises(IntegrityError):
            AssessmentChoice.objects.create(
                question=self.question, text="Dup", is_correct=False, order=1,
            )


class AssessmentAttemptTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.student = User.objects.create_user(email="student@test.com", password="x")
        now = timezone.now()
        self.assessment = Assessment.objects.create(
            title="Quiz",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            open_at=now - timezone.timedelta(days=1),
            close_at=now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            created_by=self.admin,
        )
        self.attempt = AssessmentAttempt.objects.create(
            assessment=self.assessment,
            student=self.student,
            attempt_number=1,
        )

    def test_str(self):
        self.assertIn(str(self.student), str(self.attempt))
        self.assertIn(str(self.assessment), str(self.attempt))

    def test_default_status_assigned(self):
        self.assertEqual(self.attempt.status, ATTEMPT_STATUS_ASSIGNED)

    def test_unique_assessment_student_attempt(self):
        with self.assertRaises(IntegrityError):
            AssessmentAttempt.objects.create(
                assessment=self.assessment,
                student=self.student,
                attempt_number=1,
            )

    def test_allow_second_attempt(self):
        a2 = AssessmentAttempt.objects.create(
            assessment=self.assessment,
            student=self.student,
            attempt_number=2,
        )
        self.assertEqual(a2.attempt_number, 2)


class AssessmentAnswerTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.student = User.objects.create_user(email="student@test.com", password="x")
        now = timezone.now()
        self.assessment = Assessment.objects.create(
            title="Quiz",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            open_at=now - timezone.timedelta(days=1),
            close_at=now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            created_by=self.admin,
        )
        self.question = AssessmentQuestion.objects.create(
            assessment=self.assessment,
            text="Q?",
            points=Decimal("10.00"),
            order=1,
        )
        self.attempt = AssessmentAttempt.objects.create(
            assessment=self.assessment,
            student=self.student,
            attempt_number=1,
        )
        self.answer = AssessmentAnswer.objects.create(
            attempt=self.attempt,
            question=self.question,
            written_text="My answer",
        )

    def test_str(self):
        self.assertIn("Q", str(self.answer))

    def test_unique_attempt_question(self):
        with self.assertRaises(IntegrityError):
            AssessmentAnswer.objects.create(
                attempt=self.attempt,
                question=self.question,
                written_text="Duplicate",
            )


class HomeworkSubmissionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.student = User.objects.create_user(email="student@test.com", password="x")
        now = timezone.now()
        self.assessment = Assessment.objects.create(
            title="Homework 1",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            open_at=now - timezone.timedelta(days=1),
            close_at=now + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            created_by=self.admin,
        )
        self.submission = HomeworkSubmission.objects.create(
            assessment=self.assessment,
            student=self.student,
            original_filename="essay.pdf",
            storage_key="homework/abc/essay.pdf",
            content_type="application/pdf",
            file_size=1024,
        )

    def test_str(self):
        self.assertIn("essay.pdf", str(self.submission))

    def test_default_status_uploaded(self):
        self.assertEqual(self.submission.status, HOMEWORK_STATUS_UPLOADED)


class DiplomaLectureChoiceTests(TestCase):
    """P2-1: DiplomaLectureChoice.clean() handles deleted question FK gracefully."""

    def setUp(self):
        self.admin = User.objects.create_superuser(email="dlc@test.com", password="x")
        self.diploma = Diploma.objects.create(title="DLC D", total_semesters=2, created_by=self.admin)
        self.semester = Semester.objects.create(diploma=self.diploma, order=1, title="S1")
        self.cohort = Cohort.objects.create(diploma=self.diploma, name="DLC C", start_date=date(2026, 1, 1), created_by=self.admin)
        self.lecture = DiplomaLecture.objects.create(cohort=self.cohort, semester=self.semester, order=1, title="DL")
        self.question = DiplomaLectureQuestion.objects.create(
            lecture=self.lecture, text="Q", points=Decimal("1.00"),
            question_type=QUESTION_TYPE_MCQ, active=True, created_by=self.admin,
        )

    def test_clean_with_deleted_question_fk(self):
        qid = self.question.pk
        self.question.delete()
        choice = DiplomaLectureChoice(question_id=qid, text="A", is_correct=True, order=1)
        with self.assertRaises(ValidationError) as ctx:
            choice.clean()
        self.assertIn("does not exist", str(ctx.exception))

    def test_clean_rejects_non_mcq_question(self):
        self.question.question_type = QUESTION_TYPE_WRITTEN
        self.question.save()
        choice = DiplomaLectureChoice(question=self.question, text="A", is_correct=True, order=1)
        with self.assertRaises(ValidationError) as ctx:
            choice.clean()
        self.assertIn("MCQ", str(ctx.exception))
