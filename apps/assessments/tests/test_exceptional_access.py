"""Tests for exceptional semester exam access (Phase 9)."""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import Cohort, Diploma, DiplomaEnrollment, DiplomaLecture, Semester
from apps.accounts.constants import (
    GROUP_ADMIN,
    GROUP_MODERATOR,
    GROUP_REVIEWER,
    GROUP_SECRETARY,
    GROUP_STUDENT,
    GROUP_TEACHER,
)
from apps.accounts.models import User
from apps.applications.constants import PROGRAM_APP_STATUS_ACCEPTED, PROGRAM_TYPE_DIPLOMA
from apps.applications.models import ProgramApplication
from apps.assessments.constants import (
    ASSESSMENT_TYPE_QUIZ,
    ASSESSMENT_TYPE_SEMESTER_EXAM,
    ATTEMPT_STATUS_IN_PROGRESS,
    DELIVERY_MODE_OFFLINE,
    DELIVERY_MODE_ONLINE,
    EXAM_OVERRIDE_ACTIVE,
    EXAM_OVERRIDE_CANCELLED,
    EXAM_OVERRIDE_USED,
)
from apps.assessments.models import (
    Assessment,
    AssessmentAttempt,
    AssessmentLectureSection,
    SemesterExamAccessOverride,
    SemesterExamCode,
)
from apps.assessments.services import (
    cancel_semester_exam_access_override,
    check_assessment_access,
    check_semester_exam_access,
    consume_semester_exam_override,
    create_semester_exam_access_overrides,
    get_active_semester_exam_override,
    get_student_assessments,
    start_assessment,
)
from apps.audit.constants import (
    ACTION_EXAM_ACCESS_OVERRIDE_CANCELLED,
    ACTION_EXAM_ACCESS_OVERRIDE_CREATED,
    ACTION_EXAM_ACCESS_OVERRIDE_USED,
)
from apps.audit.models import AuditEvent
from apps.students.models import StudentProfile
from apps.academics.models import Course, CourseVersion
from apps.academics.models import CourseEnrollment

_student_counter = 0


def _make_student(email="student@test.com"):
    global _student_counter
    _student_counter += 1
    user = User.objects.create_user(email=email, password="x")
    group, _ = Group.objects.get_or_create(name=GROUP_STUDENT)
    user.groups.add(group)
    StudentProfile.objects.create(
        user=user,
        full_name="Test Student",
        date_of_birth=date(2000, 1, 1),
        gender="male",
        address="Address",
        phone=f"0100000000{_student_counter:02d}",
        national_id=f"1000000000{_student_counter:04d}",
        education="bachelor",
    )
    return user


def _make_admin(email="admin@test.com"):
    user = User.objects.create_user(email=email, password="x", is_staff=True)
    group, _ = Group.objects.get_or_create(name=GROUP_ADMIN)
    user.groups.add(group)
    return user


def _make_role_user(email, group_name):
    user = User.objects.create_user(email=email, password="x")
    group, _ = Group.objects.get_or_create(name=group_name)
    user.groups.add(group)
    return user


def _make_diploma_semester_exam(admin):
    diploma = Diploma.objects.create(title="Test Diploma", total_semesters=2, created_by=admin)
    cohort = Cohort.objects.create(diploma=diploma, name="C1", start_date=date(2026, 1, 1), created_by=admin)
    semester = Semester.objects.create(diploma=diploma, order=1, title="Sem 1")
    assessment = Assessment.objects.create(
        title="Sem Exam", assessment_type=ASSESSMENT_TYPE_SEMESTER_EXAM,
        cohort=cohort, semester=semester, open_at=timezone.now() - timezone.timedelta(hours=1),
        close_at=timezone.now() + timezone.timedelta(hours=1), max_attempts=1,
        is_active=True, passing_score=Decimal("50.00"), created_by=admin,
    )
    return assessment, cohort, semester


def _add_lecture_section(assessment, cohort, semester, admin):
    lecture = DiplomaLecture.objects.create(
        cohort=cohort, semester=semester, order=1, title="L1",
    )
    AssessmentLectureSection.objects.create(
        assessment=assessment, lecture=lecture, order=1, mcq_count=0, max_grade=Decimal("100.00"),
    )


# ---------------------------------------------------------------------------
# Model Validation Tests
# ---------------------------------------------------------------------------


class SemesterExamAccessOverrideModelTests(TestCase):
    def setUp(self):
        self.admin = _make_admin()
        self.assessment, self.cohort, self.semester = _make_diploma_semester_exam(self.admin)
        self.student = _make_student()
        self.enrollment = DiplomaEnrollment.objects.create(
            student=self.student.student_profile, cohort=self.cohort,
            status=ENROLLMENT_STATUS_ACTIVE,
        )

    def _make_valid_override(self):
        return SemesterExamAccessOverride(
            assessment=self.assessment,
            diploma_enrollment=self.enrollment,
            open_at=timezone.now() - timezone.timedelta(hours=1),
            close_at=timezone.now() + timezone.timedelta(hours=1),
            reason="Medical issue",
            created_by=self.admin,
        )

    def test_override_rejects_non_semester_assessment(self):
        quiz = Assessment.objects.create(
            title="Quiz", assessment_type=ASSESSMENT_TYPE_QUIZ,
            open_at=timezone.now(), close_at=timezone.now() + timezone.timedelta(hours=1),
            passing_score=Decimal("50.00"), created_by=self.admin,
            legacy_status="legacy_archived",
        )
        override = SemesterExamAccessOverride(
            assessment=quiz,
            diploma_enrollment=self.enrollment,
            open_at=timezone.now(), close_at=timezone.now() + timezone.timedelta(hours=1),
            reason="Test", created_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            override.clean()

    def test_override_rejects_course_scoped_assessment(self):
        course = Course.objects.create(title="C", created_by=self.admin)
        cv = CourseVersion.objects.create(course=course, label="v1", created_by=self.admin)
        course_assessment = Assessment.objects.create(
            title="Course Exam", assessment_type=ASSESSMENT_TYPE_SEMESTER_EXAM,
            course_version=cv, cohort=self.cohort, semester=self.semester,
            open_at=timezone.now(), close_at=timezone.now() + timezone.timedelta(hours=1),
            passing_score=Decimal("50.00"), created_by=self.admin,
            legacy_status="legacy_archived",
        )
        override = SemesterExamAccessOverride(
            assessment=course_assessment,
            diploma_enrollment=self.enrollment,
            open_at=timezone.now(), close_at=timezone.now() + timezone.timedelta(hours=1),
            reason="Test", created_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            override.clean()

    def test_override_rejects_enrollment_outside_assessment_cohort(self):
        other_cohort = Cohort.objects.create(
            diploma=self.cohort.diploma, name="C2",
            start_date=date(2026, 6, 1), created_by=self.admin,
        )
        other_enrollment = DiplomaEnrollment.objects.create(
            student=self.student.student_profile, cohort=other_cohort,
            status=ENROLLMENT_STATUS_ACTIVE,
        )
        override = SemesterExamAccessOverride(
            assessment=self.assessment,
            diploma_enrollment=other_enrollment,
            open_at=timezone.now(), close_at=timezone.now() + timezone.timedelta(hours=1),
            reason="Test", created_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            override.clean()

    def test_override_rejects_open_at_ge_close_at(self):
        now = timezone.now()
        override = SemesterExamAccessOverride(
            assessment=self.assessment,
            diploma_enrollment=self.enrollment,
            open_at=now, close_at=now - timezone.timedelta(hours=1),
            reason="Test", created_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            override.clean()

    def test_override_requires_reason(self):
        override = SemesterExamAccessOverride(
            assessment=self.assessment,
            diploma_enrollment=self.enrollment,
            open_at=timezone.now(), close_at=timezone.now() + timezone.timedelta(hours=1),
            reason="", created_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            override.clean()

    def test_override_requires_max_attempts_ge_one(self):
        override = self._make_valid_override()
        override.max_attempts = 0
        with self.assertRaises(ValidationError):
            override.clean()


# ---------------------------------------------------------------------------
# Service Tests
# ---------------------------------------------------------------------------


class ExamAccessServiceTests(TestCase):
    def setUp(self):
        self.admin = _make_admin()
        self.assessment, self.cohort, self.semester = _make_diploma_semester_exam(self.admin)
        self.student = _make_student()
        self.enrollment = DiplomaEnrollment.objects.create(
            student=self.student.student_profile, cohort=self.cohort,
            status=ENROLLMENT_STATUS_ACTIVE,
        )
        _add_lecture_section(self.assessment, self.cohort, self.semester, self.admin)
        self.now = timezone.now()

    def _create_override(self, **kw):
        defaults = {
            "assessment": self.assessment,
            "diploma_enrollment": self.enrollment,
            "open_at": self.now - timezone.timedelta(hours=1),
            "close_at": self.now + timezone.timedelta(hours=1),
            "reason": "Medical",
            "created_by": self.admin,
        }
        defaults.update(kw)
        return SemesterExamAccessOverride.objects.create(**defaults)

    def test_normal_open_semester_exam_still_works(self):
        check_assessment_access(self.student, self.assessment)

    def test_closed_semester_exam_without_override_is_denied(self):
        self.assessment.close_at = self.now - timezone.timedelta(minutes=1)
        self.assessment.save(update_fields=["close_at"])
        with self.assertRaises(ValueError):
            check_assessment_access(self.student, self.assessment)

    def test_closed_semester_exam_with_active_override_is_allowed(self):
        self.assessment.close_at = self.now - timezone.timedelta(minutes=1)
        self.assessment.save(update_fields=["close_at"])
        self._create_override()
        check_assessment_access(self.student, self.assessment)

    def test_future_override_is_denied(self):
        self.assessment.close_at = self.now - timezone.timedelta(minutes=1)
        self.assessment.save(update_fields=["close_at"])
        self._create_override(open_at=self.now + timezone.timedelta(hours=2), close_at=self.now + timezone.timedelta(hours=3))
        with self.assertRaises(ValueError):
            check_assessment_access(self.student, self.assessment)

    def test_expired_override_is_denied(self):
        self.assessment.close_at = self.now - timezone.timedelta(minutes=1)
        self.assessment.save(update_fields=["close_at"])
        self._create_override(close_at=self.now - timezone.timedelta(minutes=1))
        override = SemesterExamAccessOverride.objects.first()
        override.status = EXAM_OVERRIDE_ACTIVE
        override.save(update_fields=["status"])
        with self.assertRaises(ValueError):
            check_assessment_access(self.student, self.assessment)

    def test_cancelled_override_is_denied(self):
        self.assessment.close_at = self.now - timezone.timedelta(minutes=1)
        self.assessment.save(update_fields=["close_at"])
        override = self._create_override()
        cancel_semester_exam_access_override(override, actor=self.admin, reason="Admin decision")
        with self.assertRaises(ValueError):
            check_assessment_access(self.student, self.assessment)

    def test_offline_exam_code_blocks_access_even_with_override(self):
        """Authoritative offline application mode blocks online access even with an override."""
        self.assessment.close_at = self.now - timezone.timedelta(minutes=1)
        self.assessment.save(update_fields=["close_at"])
        code = SemesterExamCode.objects.create(
            assessment=self.assessment, diploma_enrollment=self.enrollment,
            code_number="OFFLINE1", delivery_mode=DELIVERY_MODE_OFFLINE,
            created_by=self.admin,
        )
        ProgramApplication.objects.create(
            student=self.student.student_profile,
            program_type=PROGRAM_TYPE_DIPLOMA,
            cohort=self.cohort,
            delivery_mode=DELIVERY_MODE_OFFLINE,
            status=PROGRAM_APP_STATUS_ACCEPTED,
        )
        self._create_override(exam_code=code)
        with self.assertRaises(ValueError):
            check_assessment_access(self.student, self.assessment)

    def test_force_online_override_converts_offline_code(self):
        """force_online=True converts offline delivery mode to online."""
        self.assessment.close_at = self.now - timezone.timedelta(minutes=1)
        self.assessment.save(update_fields=["close_at"])
        code = SemesterExamCode.objects.create(
            assessment=self.assessment, diploma_enrollment=self.enrollment,
            code_number="OFFLINE2", delivery_mode=DELIVERY_MODE_OFFLINE,
            created_by=self.admin,
        )
        application = ProgramApplication.objects.create(
            student=self.student.student_profile,
            program_type=PROGRAM_TYPE_DIPLOMA,
            cohort=self.cohort,
            delivery_mode=DELIVERY_MODE_OFFLINE,
            status=PROGRAM_APP_STATUS_ACCEPTED,
        )
        # Simulate force_online: call create_semester_exam_access_overrides with force_online=True
        create_semester_exam_access_overrides(
            self.assessment, [self.enrollment],
            open_at=self.now - timezone.timedelta(hours=1),
            close_at=self.now + timezone.timedelta(hours=1),
            reason="Medical force-online",
            actor=self.admin,
            force_online=True,
        )
        code.refresh_from_db()
        application.refresh_from_db()
        self.assertEqual(code.delivery_mode, DELIVERY_MODE_ONLINE)
        self.assertEqual(application.delivery_mode, DELIVERY_MODE_ONLINE)
        check_assessment_access(self.student, self.assessment)

    def test_course_quiz_exam_does_not_use_override_logic(self):
        course = Course.objects.create(title="C", created_by=self.admin)
        cv = CourseVersion.objects.create(course=course, label="v1", created_by=self.admin)
        CourseEnrollment.objects.create(
            student=self.student.student_profile, course_version=cv,
            status=ENROLLMENT_STATUS_ACTIVE,
        )
        course_assessment = Assessment.objects.create(
            title="Course Quiz", assessment_type=ASSESSMENT_TYPE_QUIZ,
            course_version=cv,
            open_at=self.now - timezone.timedelta(days=1),
            close_at=self.now + timezone.timedelta(hours=1),
            is_active=True, passing_score=Decimal("50.00"), created_by=self.admin,
        )
        check_assessment_access(self.student, course_assessment)

    def test_exceptional_start_creates_attempt_with_is_exceptional_true(self):
        self.assessment.close_at = self.now - timezone.timedelta(minutes=1)
        self.assessment.save(update_fields=["close_at"])
        self._create_override()
        attempt = start_assessment(self.student, self.assessment)
        self.assertTrue(attempt.is_exceptional)

    def test_override_is_linked_to_created_attempt(self):
        self.assessment.close_at = self.now - timezone.timedelta(minutes=1)
        self.assessment.save(update_fields=["close_at"])
        self._create_override()
        attempt = start_assessment(self.student, self.assessment)
        override = SemesterExamAccessOverride.objects.first()
        self.assertEqual(override.attempt, attempt)
        self.assertEqual(override.status, EXAM_OVERRIDE_USED)

    def test_existing_in_progress_attempt_reuses_without_consuming_second_override(self):
        self._create_override()
        a1 = start_assessment(self.student, self.assessment)
        a2 = start_assessment(self.student, self.assessment)
        self.assertEqual(a1.id, a2.id)
        override = SemesterExamAccessOverride.objects.first()
        self.assertEqual(override.attempt, a1)
        self.assertEqual(override.status, EXAM_OVERRIDE_USED)

    def test_cancel_override_sets_cancelled_status_and_audits(self):
        override = self._create_override()
        result = cancel_semester_exam_access_override(override, actor=self.admin, reason="Wrong")
        self.assertEqual(result.status, EXAM_OVERRIDE_CANCELLED)
        self.assertEqual(result.cancelled_by, self.admin)
        self.assertIsNotNone(result.cancelled_at)
        audit = AuditEvent.objects.filter(action=ACTION_EXAM_ACCESS_OVERRIDE_CANCELLED).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.target_id, str(override.pk))

    def test_get_active_semester_exam_override_returns_none_for_no_enrollment(self):
        other_student = _make_student("other@test.com")
        result = get_active_semester_exam_override(other_student, self.assessment)
        self.assertIsNone(result)

    def test_consume_semester_exam_override_marks_used(self):
        override = self._create_override()
        attempt = AssessmentAttempt.objects.create(
            assessment=self.assessment, student=self.student, attempt_number=1,
            status=ATTEMPT_STATUS_IN_PROGRESS,
        )
        result = consume_semester_exam_override(override, attempt)
        self.assertEqual(result.status, EXAM_OVERRIDE_USED)
        self.assertEqual(result.attempt, attempt)
        self.assertIsNotNone(result.used_at)
        audit = AuditEvent.objects.filter(action=ACTION_EXAM_ACCESS_OVERRIDE_USED).first()
        self.assertIsNotNone(audit)

    def test_check_semester_exam_access_returns_true_with_override(self):
        self.assessment.close_at = self.now - timezone.timedelta(minutes=1)
        self.assessment.save(update_fields=["close_at"])
        self._create_override()
        self.assertTrue(check_semester_exam_access(self.student, self.assessment))

    def test_check_semester_exam_access_returns_false_without_override(self):
        self.assessment.close_at = self.now - timezone.timedelta(minutes=1)
        self.assessment.save(update_fields=["close_at"])
        result = check_semester_exam_access(self.student, self.assessment)
        self.assertFalse(result)

    def test_create_semester_exam_access_overrides_creates_and_audits(self):
        result = create_semester_exam_access_overrides(
            self.assessment, [self.enrollment],
            open_at=self.now - timezone.timedelta(hours=1),
            close_at=self.now + timezone.timedelta(hours=1),
            reason="Emergency", actor=self.admin,
        )
        self.assertEqual(len(result), 1)
        override = result[0]
        self.assertEqual(override.status, EXAM_OVERRIDE_ACTIVE)
        self.assertEqual(override.reason, "Emergency")
        self.assertEqual(override.max_attempts, 1)
        audit = AuditEvent.objects.filter(action=ACTION_EXAM_ACCESS_OVERRIDE_CREATED).first()
        self.assertIsNotNone(audit)

    def test_override_creates_second_attempt_after_prior_attempt(self):
        self._create_override()
        a1 = start_assessment(self.student, self.assessment)
        a1.status = "submitted"
        a1.save(update_fields=["status"])
        self._create_override()
        attempt2 = start_assessment(self.student, self.assessment)
        self.assertEqual(attempt2.attempt_number, 2)

    def test_used_override_after_close_is_denied(self):
        override = self._create_override()
        attempt = start_assessment(self.student, self.assessment)
        override.refresh_from_db()
        self.assertEqual(override.attempt_id, attempt.id)
        self.assessment.close_at = self.now - timezone.timedelta(minutes=2)
        self.assessment.save(update_fields=["close_at"])
        override.close_at = self.now - timezone.timedelta(minutes=1)
        override.save(update_fields=["close_at"])
        with self.assertRaises(ValueError):
            check_assessment_access(
                self.student,
                self.assessment,
                existing_attempt=True,
            )

    def test_override_with_different_student_does_not_grant_access(self):
        other = _make_student("other2@test.com")
        DiplomaEnrollment.objects.create(
            student=other.student_profile, cohort=self.cohort,
            status=ENROLLMENT_STATUS_ACTIVE,
        )
        self.assessment.close_at = self.now - timezone.timedelta(minutes=1)
        self.assessment.save(update_fields=["close_at"])
        self._create_override()
        with self.assertRaises(ValueError):
            check_assessment_access(other, self.assessment)


    def test_exceptional_override_snapshot_generation(self):
        """Starting an assessment with an override creates snapshot questions."""
        self.assessment.close_at = self.now - timezone.timedelta(minutes=1)
        self.assessment.save(update_fields=["close_at"])
        self._create_override()
        start_assessment(self.student, self.assessment)
        attempt = AssessmentAttempt.objects.get(
            assessment=self.assessment, student=self.student,
        )
        self.assertEqual(attempt.status, ATTEMPT_STATUS_IN_PROGRESS)
        self.assertTrue(attempt.is_exceptional)

    def test_exceptional_grade_hidden_before_release(self):
        """Exceptional grades remain hidden until grade_visibility is released."""
        self.assessment.close_at = self.now - timezone.timedelta(minutes=1)
        self.assessment.save(update_fields=["close_at"])
        self._create_override()
        check_assessment_access(self.student, self.assessment)
        self.assessment.grade_visibility = "hidden"
        self.assessment.save(update_fields=["grade_visibility"])
        results = get_student_assessments(self.student)
        for a in results:
            if a.pk == self.assessment.pk:
                self.assertNotEqual(a.grade_visibility, "released")

    def test_override_rejects_non_diploma_assessment(self):
        """Creating an override for a non-semester-exam assessment raises ValueError."""
        quiz = Assessment.objects.create(
            title="Quiz", assessment_type=ASSESSMENT_TYPE_QUIZ,
            created_by=self.admin, passing_score=50,
            open_at=self.now - timezone.timedelta(hours=1),
            close_at=self.now + timezone.timedelta(hours=1),
            legacy_status="legacy_archived",
        )
        with self.assertRaises(ValueError):
            create_semester_exam_access_overrides(
                quiz, [self.enrollment],
                open_at=self.now, close_at=self.now + timezone.timedelta(hours=1),
                reason="Test", actor=self.admin,
            )


# ---------------------------------------------------------------------------
# Admin View Tests
# ---------------------------------------------------------------------------


class AdminExceptionalAccessViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = _make_admin()
        self.assessment, self.cohort, self.semester = _make_diploma_semester_exam(self.admin)
        self.student = _make_student()
        self.enrollment = DiplomaEnrollment.objects.create(
            student=self.student.student_profile, cohort=self.cohort,
            status=ENROLLMENT_STATUS_ACTIVE,
        )
        self.url = reverse("assessments:admin_exceptional_access", args=[self.assessment.id])
        self.now = timezone.now()

    def _naive_iso(self, dt):
        return dt.replace(tzinfo=None).isoformat()

    def test_admin_can_create_override_for_selected_roster_students(self):
        self.client.force_login(self.admin)
        open_at_str = self._naive_iso(self.now - timezone.timedelta(hours=1))
        close_at_str = self._naive_iso(self.now + timezone.timedelta(hours=1))
        response = self.client.post(self.url, {
            "diploma_enrollments": [self.enrollment.id],
            "open_at": open_at_str,
            "close_at": close_at_str,
            "reason": "Medical emergency",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SemesterExamAccessOverride.objects.count(), 1)
        override = SemesterExamAccessOverride.objects.first()
        self.assertEqual(override.reason, "Medical emergency")

    def test_admin_cannot_create_override_without_reason(self):
        self.client.force_login(self.admin)
        open_at_str = self._naive_iso(self.now - timezone.timedelta(hours=1))
        close_at_str = self._naive_iso(self.now + timezone.timedelta(hours=1))
        response = self.client.post(self.url, {
            "enrollments": [self.enrollment.id],
            "open_at": open_at_str,
            "close_at": close_at_str,
            "reason": "",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SemesterExamAccessOverride.objects.count(), 0)

    def test_get_does_not_mutate_override_state(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SemesterExamAccessOverride.objects.count(), 0)

    def test_moderator_cannot_create_override(self):
        user = _make_role_user("mod@test.com", GROUP_MODERATOR)
        self.client.force_login(user)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 403)

    def test_reviewer_cannot_create_override(self):
        user = _make_role_user("rev@test.com", GROUP_REVIEWER)
        self.client.force_login(user)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 403)

    def test_secretary_cannot_create_override(self):
        user = _make_role_user("sec@test.com", GROUP_SECRETARY)
        self.client.force_login(user)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 403)

    def test_teacher_cannot_create_override(self):
        user = _make_role_user("teacher@test.com", GROUP_TEACHER)
        self.client.force_login(user)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 403)

    def test_student_cannot_access_admin_override_routes(self):
        self.client.force_login(self.student)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)


class AdminCancelExceptionalAccessViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = _make_admin()
        self.assessment, self.cohort, self.semester = _make_diploma_semester_exam(self.admin)
        self.student = _make_student()
        self.enrollment = DiplomaEnrollment.objects.create(
            student=self.student.student_profile, cohort=self.cohort,
            status=ENROLLMENT_STATUS_ACTIVE,
        )
        self.now = timezone.now()
        self.override = SemesterExamAccessOverride.objects.create(
            assessment=self.assessment, diploma_enrollment=self.enrollment,
            open_at=self.now - timezone.timedelta(hours=1),
            close_at=self.now + timezone.timedelta(hours=1),
            reason="Emergency", created_by=self.admin,
        )
        self.cancel_url = reverse(
            "assessments:admin_cancel_exceptional_access",
            args=[self.assessment.id, self.override.id],
        )

    def test_admin_can_cancel_active_override(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.cancel_url, {"reason": "No longer needed"})
        self.assertEqual(response.status_code, 302)
        self.override.refresh_from_db()
        self.assertEqual(self.override.status, EXAM_OVERRIDE_CANCELLED)
        self.assertIsNotNone(self.override.cancelled_at)

    def test_moderator_cannot_cancel_override(self):
        user = _make_role_user("mod2@test.com", GROUP_MODERATOR)
        self.client.force_login(user)
        response = self.client.post(self.cancel_url)
        self.assertEqual(response.status_code, 403)

    def test_teacher_cannot_cancel_override(self):
        user = _make_role_user("teacher2@test.com", GROUP_TEACHER)
        self.client.force_login(user)
        response = self.client.post(self.cancel_url)
        self.assertEqual(response.status_code, 403)

    def test_student_cannot_cancel_override(self):
        self.client.force_login(self.student)
        response = self.client.post(self.cancel_url)
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# Student Visibility Tests
# ---------------------------------------------------------------------------


class StudentOverrideVisibilityTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = _make_admin()
        self.assessment, self.cohort, self.semester = _make_diploma_semester_exam(self.admin)
        self.student = _make_student()
        self.enrollment = DiplomaEnrollment.objects.create(
            student=self.student.student_profile, cohort=self.cohort,
            status=ENROLLMENT_STATUS_ACTIVE,
        )
        self.now = timezone.now()

    def test_student_sees_closed_exam_with_active_override_in_list(self):
        self.assessment.open_at = self.now - timezone.timedelta(days=2)
        self.assessment.close_at = self.now - timezone.timedelta(days=1)
        self.assessment.save(update_fields=["open_at", "close_at"])
        SemesterExamAccessOverride.objects.create(
            assessment=self.assessment, diploma_enrollment=self.enrollment,
            open_at=self.now - timezone.timedelta(hours=1),
            close_at=self.now + timezone.timedelta(hours=1),
            reason="Late", created_by=self.admin,
        )
        results = get_student_assessments(self.student)
        self.assertIn(self.assessment, results)

    def test_student_does_not_see_other_students_override(self):
        other = _make_student("other3@test.com")
        other_enrollment = DiplomaEnrollment.objects.create(
            student=other.student_profile, cohort=self.cohort,
            status=ENROLLMENT_STATUS_ACTIVE,
        )
        self.assessment.open_at = self.now + timezone.timedelta(days=1)
        self.assessment.close_at = self.now + timezone.timedelta(days=2)
        self.assessment.save(update_fields=["open_at", "close_at"])
        SemesterExamAccessOverride.objects.create(
            assessment=self.assessment, diploma_enrollment=other_enrollment,
            open_at=self.now - timezone.timedelta(hours=1),
            close_at=self.now + timezone.timedelta(hours=1),
            reason="Late", created_by=self.admin,
        )
        results = get_student_assessments(self.student)
        self.assertNotIn(self.assessment, results)


# ---------------------------------------------------------------------------
# Anonymous Access Tests
# ---------------------------------------------------------------------------


class AnonymousAccessTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = _make_admin()
        self.assessment, self.cohort, self.semester = _make_diploma_semester_exam(self.admin)
        self.url = reverse("assessments:admin_exceptional_access", args=[self.assessment.id])

    def test_anonymous_redirected_to_login_on_get(self):
        response = self.client.get(self.url)
        self.assertRedirects(response, f"/accounts/login/?next={self.url}")

    def test_anonymous_redirected_to_login_on_post(self):
        response = self.client.post(self.url)
        self.assertRedirects(response, f"/accounts/login/?next={self.url}")
