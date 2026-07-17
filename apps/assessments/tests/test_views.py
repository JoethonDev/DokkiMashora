"""Tests for the assessments app views."""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import (
    Cohort,
    Course,
    CourseEnrollment,
    CourseVersion,
    Diploma,
    DiplomaEnrollment,
    DiplomaLecture,
    ModeratorAssignment,
    Semester,
    StudentCluster,
    StudentClusterMembership,
)
from apps.accounts.constants import GROUP_MODERATOR, GROUP_STUDENT, GROUP_TEACHER
from apps.accounts.models import User
from apps.assessments.constants import (
    ASSESSMENT_TYPE_HOMEWORK,
    ASSESSMENT_TYPE_QUIZ,
    ATTEMPT_STATUS_IN_PROGRESS,
    ATTEMPT_STATUS_SUBMITTED,
    ATTEMPT_STATUS_UNDER_REVIEW,
    HOMEWORK_STATUS_UNDER_REVIEW,
    QUESTION_TYPE_MCQ,
)
from apps.assessments.models import (
    Assessment,
    AssessmentAttempt,
    AssessmentChoice,
    AssessmentLectureSection,
    AssessmentQuestion,
    AssessmentSectionManualQuestion,
    DiplomaLectureChoice,
    DiplomaLectureQuestion,
    HomeworkSubmission,
)
from apps.students.models import StudentProfile


def _create_profile(user: User, *, national_id: str, phone: str) -> StudentProfile:
    return StudentProfile.objects.create(
        user=user,
        full_name="Test Student",
        date_of_birth=date(2000, 1, 1),
        gender="male",
        address="Address",
        phone=phone,
        national_id=national_id,
        education="bachelor",
        specialization="Spec",
        employer="Employer",
        current_job="Job",
        church="Church",
        priest_name="Priest",
        priest_contact="01000000000",
    )


class StudentListViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(email="student@test.com", password="x")
        _create_profile(self.user, national_id="10000000000001", phone="01000000001")
        self.client.force_login(self.user)
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.first = Assessment.objects.create(
            title="First Quiz",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            open_at=timezone.now() - timezone.timedelta(days=1),
            close_at=timezone.now() + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            is_active=True,
            created_by=self.admin,
        )
        self.second = Assessment.objects.create(
            title="Second Quiz",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            open_at=timezone.now() - timezone.timedelta(days=1),
            close_at=timezone.now() + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            is_active=True,
            created_by=self.admin,
        )
        AssessmentAttempt.objects.create(
            assessment=self.first,
            student=self.user,
            attempt_number=1,
            status=ATTEMPT_STATUS_IN_PROGRESS,
        )
        AssessmentAttempt.objects.create(
            assessment=self.second,
            student=self.user,
            attempt_number=1,
            status=ATTEMPT_STATUS_SUBMITTED,
        )

    def test_latest_attempt_is_tracked_per_assessment(self):
        response = self.client.get(reverse("assessments:student_list"))
        self.assertEqual(response.status_code, 200)
        cards = response.context["assessment_cards"]
        first_card = next(card for card in cards if card["assessment"].id == self.first.id)
        second_card = next(card for card in cards if card["assessment"].id == self.second.id)
        self.assertEqual(first_card["latest_attempt"].status, ATTEMPT_STATUS_IN_PROGRESS)
        self.assertEqual(second_card["latest_attempt"].status, ATTEMPT_STATUS_SUBMITTED)


class AssessmentInstructionsViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.student = User.objects.create_user(email="student@test.com", password="x")
        _create_profile(self.student, national_id="10000000000002", phone="01000000002")
        self.client.force_login(self.student)
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.course = Course.objects.create(title="Course", created_by=self.admin)
        self.course_version = CourseVersion.objects.create(
            course=self.course,
            label="v1",
            created_by=self.admin,
        )
        self.assessment = Assessment.objects.create(
            title="Restricted Quiz",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            course_version=self.course_version,
            open_at=timezone.now() - timezone.timedelta(days=1),
            close_at=timezone.now() + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            is_active=True,
            created_by=self.admin,
        )

    def test_unenrolled_student_is_redirected(self):
        response = self.client.get(reverse("assessments:instructions", args=[self.assessment.id]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("assessments:student_list"), response.url)


class HomeworkTakeViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.student = User.objects.create_user(email="student@test.com", password="x")
        _create_profile(self.student, national_id="10000000000005", phone="01000000005")
        self.client.force_login(self.student)
        self.admin = User.objects.create_superuser(email="admin@test.com", password="x")
        self.course = Course.objects.create(title="Course", created_by=self.admin)
        self.course_version = CourseVersion.objects.create(
            course=self.course,
            label="v1",
            created_by=self.admin,
        )
        self.assessment = Assessment.objects.create(
            title="Homework",
            assessment_type=ASSESSMENT_TYPE_HOMEWORK,
            course_version=self.course_version,
            open_at=timezone.now() - timezone.timedelta(days=1),
            close_at=timezone.now() + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            is_active=True,
            created_by=self.admin,
        )

    def test_unenrolled_student_is_redirected_from_take_page(self):
        response = self.client.get(reverse("assessments:take", args=[self.assessment.id]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("assessments:student_list"), response.url)


class HomeworkSubmittedViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.student = User.objects.create_user(email="student2@test.com", password="x")
        _create_profile(self.student, national_id="10000000000006", phone="01000000006")
        self.client.force_login(self.student)
        self.admin = User.objects.create_superuser(email="admin2@test.com", password="x")
        self.course = Course.objects.create(title="Course", created_by=self.admin)
        self.course_version = CourseVersion.objects.create(
            course=self.course,
            label="v1",
            created_by=self.admin,
        )
        CourseEnrollment.objects.create(
            student=self.student.student_profile,
            course_version=self.course_version,
            status=ENROLLMENT_STATUS_ACTIVE,
        )
        self.assessment = Assessment.objects.create(
            title="Homework",
            assessment_type=ASSESSMENT_TYPE_HOMEWORK,
            course_version=self.course_version,
            open_at=timezone.now() - timezone.timedelta(days=1),
            close_at=timezone.now() + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            is_active=True,
            created_by=self.admin,
        )
        self.attempt = AssessmentAttempt.objects.create(
            assessment=self.assessment,
            student=self.student,
            attempt_number=1,
            status=ATTEMPT_STATUS_SUBMITTED,
        )
        self.submission = HomeworkSubmission.objects.create(
            assessment=self.assessment,
            student=self.student,
            attempt=self.attempt,
            original_filename="homework.pdf",
            storage_key="homework/1/homework.pdf",
            content_type="application/pdf",
            file_size=1024,
            submitted_at=timezone.now(),
            status=HOMEWORK_STATUS_UNDER_REVIEW,
        )

    def test_take_page_hides_upload_form_after_submission(self):
        response = self.client.get(reverse("assessments:take", args=[self.assessment.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.submission.original_filename)
        self.assertNotContains(response, "Upload your homework files")

    def test_results_page_exposes_download_link(self):
        response = self.client.get(reverse("assessments:results", args=[self.assessment.id]))
        self.assertEqual(response.status_code, 200)
        # The download link href is untranslated, so check for it directly.
        # The button label is translated per request locale.
        self.assertContains(response, "/media/storage/homework/1/homework.pdf")


class AdminPreviewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(email="preview-admin@test.com", password="x")
        self.client.force_login(self.admin)
        self.course = Course.objects.create(title="Preview Course", created_by=self.admin)
        self.course_version = CourseVersion.objects.create(
            course=self.course, label="v1", created_by=self.admin,
        )
        self.assessment = Assessment.objects.create(
            title="Preview Quiz",
            assessment_type="quiz",
            course_version=self.course_version,
            open_at=timezone.now(),
            close_at=timezone.now() + timezone.timedelta(days=1),
            duration_minutes=30,
            passing_score=Decimal("50.00"),
            created_by=self.admin,
        )
        self.question = AssessmentQuestion.objects.create(
            assessment=self.assessment, text="q?", question_type="mcq", order=1, points=5,
        )
        AssessmentChoice.objects.create(question=self.question, text="a", order=1, is_correct=True)

    def test_admin_preview_renders_banner_without_submit(self):
        response = self.client.get(reverse("assessments:admin_preview", args=[self.assessment.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Preview")
        self.assertNotContains(response, "Submit assessment")


class AdminGradePermissionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(email="admin-grade@test.com", password="x")
        self.teacher = User.objects.create_user(email="unassigned-teacher@test.com", password="x")
        group, _ = Group.objects.get_or_create(name=GROUP_TEACHER)
        self.teacher.groups.add(group)
        self.student = User.objects.create_user(email="grade-student@test.com", password="x")
        _create_profile(self.student, national_id="10000000000106", phone="01000000106")
        diploma = Diploma.objects.create(title="Grade Diploma", total_semesters=2, created_by=self.admin)
        cohort = Cohort.objects.create(
            diploma=diploma,
            name="Grade Cohort",
            start_date=timezone.now().date(),
            created_by=self.admin,
        )
        course = Course.objects.create(title="Grade Course", created_by=self.admin)
        course_version = CourseVersion.objects.create(
            course=course,
            cohort=cohort,
            label="v1",
            created_by=self.admin,
        )
        self.assessment = Assessment.objects.create(
            title="Scoped Grade Quiz",
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            course_version=course_version,
            open_at=timezone.now() - timezone.timedelta(days=1),
            close_at=timezone.now() + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"),
            is_active=True,
            created_by=self.admin,
        )
        self.attempt = AssessmentAttempt.objects.create(
            assessment=self.assessment,
            student=self.student,
            attempt_number=1,
            status=ATTEMPT_STATUS_UNDER_REVIEW,
        )

    def test_unassigned_teacher_direct_grade_url_is_forbidden(self):
        self.client.force_login(self.teacher)

        response = self.client.get(reverse("assessments:admin_grade_detail", args=[self.attempt.id]))

        self.assertEqual(response.status_code, 403)


class ModeratorCrossScopeTests(TestCase):
    """Cluster-scoped moderator CANNOT grade attempts of students outside their cluster."""

    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(email="mod-scope-admin@test.com", password="x")
        cohort = Cohort.objects.create(
            diploma=Diploma.objects.create(title="Mod Diploma", total_semesters=2, created_by=self.admin),
            name="Mod Cohort", start_date=date(2026, 1, 1), created_by=self.admin,
        )
        cluster_a = StudentCluster.objects.create(name="A", cohort=cohort, created_by=self.admin)
        cluster_b = StudentCluster.objects.create(name="B", cohort=cohort, created_by=self.admin)
        self.student_a = User.objects.create_user(email="student-a@test.com", password="x")
        _create_profile(self.student_a, national_id="10000000000201", phone="01000000201")
        self.student_b = User.objects.create_user(email="student-b@test.com", password="x")
        _create_profile(self.student_b, national_id="10000000000202", phone="01000000202")
        DiplomaEnrollment.objects.create(student=self.student_a.student_profile, cohort=cohort, status=ENROLLMENT_STATUS_ACTIVE)
        DiplomaEnrollment.objects.create(student=self.student_b.student_profile, cohort=cohort, status=ENROLLMENT_STATUS_ACTIVE)
        StudentClusterMembership.objects.create(student=self.student_a.student_profile, cluster=cluster_a, assigned_by=self.admin)
        StudentClusterMembership.objects.create(student=self.student_b.student_profile, cluster=cluster_b, assigned_by=self.admin)
        course = Course.objects.create(title="Mod Course", created_by=self.admin)
        course_version = CourseVersion.objects.create(course=course, label="v1", created_by=self.admin)
        self.assessment = Assessment.objects.create(
            title="Mod Quiz", assessment_type=ASSESSMENT_TYPE_QUIZ,
            course_version=course_version,
            open_at=timezone.now() - timezone.timedelta(days=1),
            close_at=timezone.now() + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"), is_active=True, created_by=self.admin,
        )
        self.attempt_a = AssessmentAttempt.objects.create(
            assessment=self.assessment, student=self.student_a, attempt_number=1, status=ATTEMPT_STATUS_UNDER_REVIEW,
        )
        self.attempt_b = AssessmentAttempt.objects.create(
            assessment=self.assessment, student=self.student_b, attempt_number=2, status=ATTEMPT_STATUS_UNDER_REVIEW,
        )
        self.moderator = User.objects.create_user(email="moderator@test.com", password="x")
        group, _ = Group.objects.get_or_create(name=GROUP_MODERATOR)
        self.moderator.groups.add(group)
        ModeratorAssignment.objects.create(
            moderator=self.moderator, course_version=course_version,
            cohort=cohort, cluster=cluster_a, active=True, assigned_by=self.admin,
        )

    def test_cluster_moderator_can_grade_own_cluster_student(self):
        self.client.force_login(self.moderator)
        response = self.client.get(reverse("assessments:admin_grade_detail", args=[self.attempt_a.id]))
        self.assertEqual(response.status_code, 200)

    def test_cluster_moderator_cannot_grade_other_cluster_student(self):
        self.client.force_login(self.moderator)
        response = self.client.get(reverse("assessments:admin_grade_detail", args=[self.attempt_b.id]))
        self.assertEqual(response.status_code, 403)


class AssessmentScopeDependencyTests(TestCase):
    """HTMX scope-fields endpoint returns correct partials for each scope type."""

    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(email="admin@scope.com", password="x")
        self.client.force_login(self.admin)
        self.diploma = Diploma.objects.create(title="Scope Diploma", total_semesters=2, created_by=self.admin)
        self.cohort = Cohort.objects.create(
            diploma=self.diploma, name="Scope Cohort",
            start_date=date(2026, 1, 1), created_by=self.admin,
        )
        self.course = Course.objects.create(title="Scope Course", created_by=self.admin)

    def test_scope_standalone_course_shows_course_field(self):
        response = self.client.get(
            reverse("assessments:admin_scope_fields"),
            {"scope_type": "course"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "standalone_course")
        self.assertContains(response, "scope-detail-fields")

    def test_scope_diploma_shows_diploma_selector(self):
        response = self.client.get(
            reverse("assessments:admin_scope_fields"),
            {"scope_type": "diploma"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "diploma")
        self.assertNotContains(response, "semester")
        self.assertNotContains(response, "cohort")

    def test_scope_diploma_with_diploma_shows_semester(self):
        response = self.client.get(reverse("assessments:admin_scope_fields"), {
            "scope_type": "diploma",
            "diploma": str(self.diploma.id),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "semester")

    def test_scope_diploma_with_semester_shows_cohort(self):
        semester = self.diploma.semesters.create(title="Sem1", order=1)
        response = self.client.get(reverse("assessments:admin_scope_fields"), {
            "scope_type": "diploma",
            "diploma": str(self.diploma.id),
            "semester": str(semester.id),
            "cohort": str(self.cohort.id),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cohort")

    def test_scope_get_cannot_save(self):
        """GET scope endpoint must not create or update assessments."""
        response = self.client.get(
            reverse("assessments:admin_scope_fields"),
            {"scope_type": "course", "standalone_course": str(self.course.id)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Assessment.objects.count(), 0)


class DiplomaLectureBankTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="bank-admin@test.com", password="x")
        self.student = User.objects.create_user(email="bank-student@test.com", password="x")
        group, _ = Group.objects.get_or_create(name=GROUP_STUDENT)
        self.student.groups.add(group)
        self.diploma = Diploma.objects.create(title="Bank Diploma", total_semesters=2, created_by=self.admin)
        self.cohort = Cohort.objects.create(diploma=self.diploma, name="Bank Cohort", start_date=date(2026, 1, 1), created_by=self.admin)
        self.semester = Semester.objects.create(diploma=self.diploma, title="Bank Sem", order=1)
        self.lecture = DiplomaLecture.objects.create(
            cohort=self.cohort, semester=self.semester, order=1,
            title="Bank Lecture",
        )
        self.list_url = reverse("assessments:admin_lecture_bank_list", args=[self.diploma.id, self.cohort.id, self.semester.id, self.lecture.id])
        self.create_url = reverse("assessments:admin_lecture_bank_question_create", args=[self.diploma.id, self.cohort.id, self.semester.id, self.lecture.id])

    def _edit_url(self, qid):
        return reverse("assessments:admin_lecture_bank_question_edit", args=[self.diploma.id, self.cohort.id, self.semester.id, self.lecture.id, qid])

    def _delete_url(self, qid):
        return reverse("assessments:admin_lecture_bank_question_delete", args=[self.diploma.id, self.cohort.id, self.semester.id, self.lecture.id, qid])

    def test_admin_can_create_bank_question_with_choices(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.create_url, {
            "text": "Sample MCQ?",
            "question_type": QUESTION_TYPE_MCQ,
            "points": "2.00",
            "active": "on",
            "choice_text_new_1": "Correct answer",
            "choice_correct_new_1": "1",
            "choice_order_new_1": "1",
            "choice_text_new_2": "Wrong answer",
            "choice_order_new_2": "2",
        })
        self.assertRedirects(response, self.list_url)
        self.assertEqual(DiplomaLectureQuestion.objects.count(), 1)
        q = DiplomaLectureQuestion.objects.first()
        self.assertEqual(q.text, "Sample MCQ?")
        self.assertEqual(q.choices.count(), 2)
        self.assertEqual(q.choices.filter(is_correct=True).count(), 1)

    def test_admin_can_edit_bank_question(self):
        self.client.force_login(self.admin)
        q = DiplomaLectureQuestion.objects.create(lecture=self.lecture, text="Old", question_type=QUESTION_TYPE_MCQ, points=Decimal("1.00"), created_by=self.admin)
        DiplomaLectureChoice.objects.create(question=q, text="Old A", is_correct=True, order=1)
        DiplomaLectureChoice.objects.create(question=q, text="Old B", is_correct=False, order=2)
        edit_url = self._edit_url(q.id)
        response = self.client.post(edit_url, {
            "text": "Updated?",
            "question_type": QUESTION_TYPE_MCQ,
            "points": "3.00",
            "active": "on",
            "choice_text_" + str(q.choices.first().id): "New A",
            "choice_correct_" + str(q.choices.first().id): "1",
            "choice_order_" + str(q.choices.first().id): "1",
            "choice_text_new_1": "New B",
            "choice_order_new_1": "2",
        })
        self.assertRedirects(response, self.list_url)
        q.refresh_from_db()
        self.assertEqual(q.text, "Updated?")
        self.assertEqual(q.points, Decimal("3.00"))
        self.assertEqual(q.choices.count(), 2)

    def test_admin_can_delete_bank_question(self):
        self.client.force_login(self.admin)
        q = DiplomaLectureQuestion.objects.create(lecture=self.lecture, text="Delete me", question_type=QUESTION_TYPE_MCQ, points=Decimal("1.00"), created_by=self.admin)
        response = self.client.post(self._delete_url(q.id))
        self.assertRedirects(response, self.list_url)
        self.assertEqual(DiplomaLectureQuestion.objects.count(), 0)

    def test_create_question_with_less_than_two_choices_fails(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.create_url, {
            "text": "Bad?",
            "question_type": QUESTION_TYPE_MCQ,
            "points": "1.00",
            "active": "on",
            "choice_text_new_1": "Only one choice",
            "choice_correct_new_1": "1",
            "choice_order_new_1": "1",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(len(response.context["form"].non_field_errors()) > 0)
        self.assertEqual(DiplomaLectureQuestion.objects.count(), 0)

    def test_create_question_without_exactly_one_correct_fails(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.create_url, {
            "text": "Bad?",
            "question_type": QUESTION_TYPE_MCQ,
            "points": "1.00",
            "active": "on",
            "choice_text_new_1": "Choice A",
            "choice_order_new_1": "1",
            "choice_text_new_2": "Choice B",
            "choice_correct_new_2": "1",
            "choice_order_new_2": "2",
            "choice_text_new_3": "Choice C",
            "choice_correct_new_3": "1",
            "choice_order_new_3": "3",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(len(response.context["form"].non_field_errors()) > 0)
        self.assertEqual(DiplomaLectureQuestion.objects.count(), 0)

    def test_student_cannot_access_bank_list(self):
        self.client.force_login(self.student)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 403)

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(self.list_url)
        self.assertRedirects(response, "/en/accounts/login/?next=" + self.list_url)

    def test_list_shows_questions(self):
        self.client.force_login(self.admin)
        q = DiplomaLectureQuestion.objects.create(lecture=self.lecture, text="Visible?", question_type=QUESTION_TYPE_MCQ, points=Decimal("1.00"), created_by=self.admin)
        DiplomaLectureChoice.objects.create(question=q, text="Yes", is_correct=True, order=1)
        DiplomaLectureChoice.objects.create(question=q, text="No", is_correct=False, order=2)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Visible?")
        self.assertContains(response, "Yes")
        self.assertContains(response, "No")


class DiplomaSectionManagementTests(TestCase):
    """T3: Admin lecture section composition for diploma assessments."""

    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(email="section-admin@test.com", password="x")
        self.student = User.objects.create_user(email="section-student@test.com", password="x")
        group, _ = Group.objects.get_or_create(name=GROUP_STUDENT)
        self.student.groups.add(group)
        self.diploma = Diploma.objects.create(title="Section Diploma", total_semesters=2, created_by=self.admin)
        self.cohort = Cohort.objects.create(diploma=self.diploma, name="Section Cohort", start_date=date(2026, 1, 1), created_by=self.admin)
        self.semester = Semester.objects.create(diploma=self.diploma, title="Section Sem", order=1)
        self.lecture = DiplomaLecture.objects.create(
            cohort=self.cohort, semester=self.semester, order=1,
            title="Section Lecture",
        )
        # Create enough bank MCQs for section tests
        for i in range(5):
            q = DiplomaLectureQuestion.objects.create(
                lecture=self.lecture, text=f"Bank Q{i}", points=Decimal("10.00"),
                question_type=QUESTION_TYPE_MCQ, active=True, created_by=self.admin,
            )
            DiplomaLectureChoice.objects.create(question=q, text="A", is_correct=True, order=1)
            DiplomaLectureChoice.objects.create(question=q, text="B", is_correct=False, order=2)
        self.wrong_cohort = Cohort.objects.create(diploma=self.diploma, name="Wrong Cohort", start_date=date(2026, 2, 1), created_by=self.admin)
        self.wrong_lecture = DiplomaLecture.objects.create(
            cohort=self.wrong_cohort, semester=self.semester, order=2,
            title="Wrong Lecture",
        )
        self.course = Course.objects.create(title="Section Course", created_by=self.admin)
        self.course_version = CourseVersion.objects.create(course=self.course, label="v1", created_by=self.admin)
        self.course_assessment = Assessment.objects.create(
            title="Course Scoped", assessment_type="quiz",
            course_version=self.course_version,
            open_at=timezone.now() - timezone.timedelta(days=1),
            close_at=timezone.now() + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"), total_points=Decimal("100.00"),
            created_by=self.admin,
        )
        self.diploma_assessment = Assessment.objects.create(
            title="Diploma Scoped", assessment_type="quiz",
            semester=self.semester, cohort=self.cohort,
            open_at=timezone.now() - timezone.timedelta(days=1),
            close_at=timezone.now() + timezone.timedelta(days=7),
            passing_score=Decimal("50.00"), total_points=Decimal("100.00"),
            created_by=self.admin,
        )
        self.detail_url = reverse("assessments:admin_detail", args=[self.diploma_assessment.id])
        self.create_url = reverse("assessments:admin_section_create", args=[self.diploma_assessment.id])
        self.course_detail_url = reverse("assessments:admin_detail", args=[self.course_assessment.id])
        self.course_create_url = reverse("assessments:admin_section_create", args=[self.course_assessment.id])

    def _edit_url(self, section_id):
        return reverse("assessments:admin_section_edit", args=[self.diploma_assessment.id, section_id])

    def _delete_url(self, section_id):
        return reverse("assessments:admin_section_delete", args=[self.diploma_assessment.id, section_id])

    def _manual_create_url(self, section_id):
        return reverse("assessments:admin_section_manual_create", args=[self.diploma_assessment.id, section_id])

    def _manual_edit_url(self, section_id, mq_id):
        return reverse("assessments:admin_section_manual_edit", args=[self.diploma_assessment.id, section_id, mq_id])

    def _manual_delete_url(self, section_id, mq_id):
        return reverse("assessments:admin_section_manual_delete", args=[self.diploma_assessment.id, section_id, mq_id])

    # --- Section CRUD ---

    def test_admin_can_create_lecture_section(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.create_url, {
            "lecture": self.lecture.id, "order": 1, "mcq_count": 5, "max_grade": "50.00",
        })
        self.assertRedirects(response, self.detail_url)
        self.assertEqual(self.diploma_assessment.lecture_sections.count(), 1)
        section = self.diploma_assessment.lecture_sections.first()
        self.assertEqual(section.lecture_id, self.lecture.id)
        self.assertEqual(section.mcq_count, 5)
        self.assertEqual(section.max_grade, Decimal("50.00"))

    def test_admin_can_edit_lecture_section(self):
        self.client.force_login(self.admin)
        section = AssessmentLectureSection.objects.create(
            assessment=self.diploma_assessment, lecture=self.lecture, order=1, mcq_count=3, max_grade=Decimal("30.00"),
        )
        edit_url = self._edit_url(section.id)
        response = self.client.post(edit_url, {
            "lecture": self.lecture.id, "order": 2, "mcq_count": 4, "max_grade": "80.00",
        })
        self.assertRedirects(response, self.detail_url)
        section.refresh_from_db()
        self.assertEqual(section.order, 2)
        self.assertEqual(section.mcq_count, 4)
        self.assertEqual(section.max_grade, Decimal("80.00"))

    def test_admin_can_delete_lecture_section(self):
        self.client.force_login(self.admin)
        section = AssessmentLectureSection.objects.create(
            assessment=self.diploma_assessment, lecture=self.lecture, order=1, mcq_count=3, max_grade=Decimal("30.00"),
        )
        response = self.client.post(self._delete_url(section.id))
        self.assertRedirects(response, self.detail_url)
        self.assertEqual(self.diploma_assessment.lecture_sections.count(), 0)

    def test_section_create_get_renders_form(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.create_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "lecture")
        self.assertContains(response, "order")

    def test_cannot_create_section_on_course_scoped_assessment(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.course_create_url, {
            "lecture": self.lecture.id, "order": 1, "mcq_count": 5, "max_grade": "50.00",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.course_assessment.lecture_sections.count(), 0)
        self.assertIn("lecture", response.context["form"].errors) if response.context.get("form") else None

    def test_section_rejects_lecture_from_wrong_cohort(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.create_url, {
            "lecture": self.wrong_lecture.id, "order": 1, "mcq_count": 5, "max_grade": "50.00",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.diploma_assessment.lecture_sections.count(), 0)

    # --- Manual question CRUD ---

    def test_admin_can_create_manual_question(self):
        self.client.force_login(self.admin)
        section = AssessmentLectureSection.objects.create(
            assessment=self.diploma_assessment, lecture=self.lecture, order=1, mcq_count=0, max_grade=Decimal("10.00"),
        )
        url = self._manual_create_url(section.id)
        response = self.client.post(url, {
            "text": "Written question?", "question_type": "written", "points": "5.00", "order": 1, "required": "on",
        })
        self.assertRedirects(response, self.detail_url)
        self.assertEqual(section.manual_questions.count(), 1)
        mq = section.manual_questions.first()
        self.assertEqual(mq.text, "Written question?")
        self.assertEqual(mq.points, Decimal("5.00"))

    def test_admin_can_edit_manual_question(self):
        self.client.force_login(self.admin)
        section = AssessmentLectureSection.objects.create(
            assessment=self.diploma_assessment, lecture=self.lecture, order=1, mcq_count=0, max_grade=Decimal("10.00"),
        )
        mq = AssessmentSectionManualQuestion.objects.create(
            section=section, text="Old", points=Decimal("2.00"), order=1,
        )
        url = self._manual_edit_url(section.id, mq.id)
        response = self.client.post(url, {
            "text": "Updated?", "question_type": "written", "points": "7.00", "order": 2,
        })
        self.assertRedirects(response, self.detail_url)
        mq.refresh_from_db()
        self.assertEqual(mq.text, "Updated?")
        self.assertEqual(mq.points, Decimal("7.00"))
        self.assertEqual(mq.order, 2)

    def test_admin_can_delete_manual_question(self):
        self.client.force_login(self.admin)
        section = AssessmentLectureSection.objects.create(
            assessment=self.diploma_assessment, lecture=self.lecture, order=1, mcq_count=0, max_grade=Decimal("10.00"),
        )
        mq = AssessmentSectionManualQuestion.objects.create(
            section=section, text="Delete me", points=Decimal("1.00"), order=1,
        )
        url = self._manual_delete_url(section.id, mq.id)
        response = self.client.post(url)
        self.assertRedirects(response, self.detail_url)
        self.assertEqual(section.manual_questions.count(), 0)

    # --- Activation validation ---

    def test_validate_sections_ready_insufficient_mcqs(self):
        section = AssessmentLectureSection.objects.create(
            assessment=self.diploma_assessment, lecture=self.lecture, order=1, mcq_count=99, max_grade=Decimal("100.00"),
        )
        from apps.assessments.services import validate_sections_ready
        with self.assertRaisesMessage(ValueError, "requires 99 MCQs"):
            validate_sections_ready(self.diploma_assessment)

    def test_validate_sections_ready_total_grade_mismatch(self):
        AssessmentLectureSection.objects.create(
            assessment=self.diploma_assessment, lecture=self.lecture, order=1, mcq_count=0, max_grade=Decimal("50.00"),
        )
        from apps.assessments.services import validate_sections_ready
        with self.assertRaisesMessage(ValueError, "does not match"):
            validate_sections_ready(self.diploma_assessment)

    def test_validate_sections_ready_success(self):
        DiplomaLectureQuestion.objects.create(
            lecture=self.lecture, text="Q1", question_type=QUESTION_TYPE_MCQ, points=Decimal("1.00"), active=True, created_by=self.admin,
        )
        DiplomaLectureQuestion.objects.create(
            lecture=self.lecture, text="Q2", question_type=QUESTION_TYPE_MCQ, points=Decimal("1.00"), active=True, created_by=self.admin,
        )
        AssessmentLectureSection.objects.create(
            assessment=self.diploma_assessment, lecture=self.lecture, order=1, mcq_count=2, max_grade=Decimal("100.00"),
        )
        from apps.assessments.services import validate_sections_ready
        validate_sections_ready(self.diploma_assessment)

    # --- Course assessment detail still shows questions ---

    def test_course_assessment_detail_shows_questions_tab(self):
        self.client.force_login(self.admin)
        q = AssessmentQuestion.objects.create(
            assessment=self.course_assessment, text="Course Q?", question_type=QUESTION_TYPE_MCQ, points=Decimal("5.00"), order=1,
        )
        response = self.client.get(self.course_detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Course Q?")

    def test_diploma_assessment_detail_shows_sections(self):
        self.client.force_login(self.admin)
        AssessmentLectureSection.objects.create(
            assessment=self.diploma_assessment, lecture=self.lecture, order=1, mcq_count=3, max_grade=Decimal("50.00"),
        )
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.lecture.title)

    # --- Student cannot access section management ---

    def test_student_cannot_access_section_create(self):
        self.client.force_login(self.student)
        response = self.client.get(self.create_url)
        self.assertEqual(response.status_code, 403)

    def test_student_cannot_access_section_edit(self):
        self.client.force_login(self.student)
        section = AssessmentLectureSection.objects.create(
            assessment=self.diploma_assessment, lecture=self.lecture, order=1, mcq_count=3, max_grade=Decimal("50.00"),
        )
        response = self.client.get(self._edit_url(section.id))
        self.assertEqual(response.status_code, 403)

    def test_student_cannot_post_section_delete(self):
        self.client.force_login(self.student)
        section = AssessmentLectureSection.objects.create(
            assessment=self.diploma_assessment, lecture=self.lecture, order=1, mcq_count=3, max_grade=Decimal("50.00"),
        )
        response = self.client.post(self._delete_url(section.id))
        self.assertEqual(response.status_code, 403)

    # --- Anonymous redirects to login ---

    def test_anonymous_redirected_to_login_on_section_create(self):
        response = self.client.get(self.create_url)
        self.assertRedirects(response, "/en/accounts/login/?next=" + self.create_url)

    def test_anonymous_redirected_to_login_on_section_edit(self):
        section = AssessmentLectureSection.objects.create(
            assessment=self.diploma_assessment, lecture=self.lecture, order=1, mcq_count=3, max_grade=Decimal("50.00"),
        )
        response = self.client.get(self._edit_url(section.id))
        self.assertRedirects(response, "/en/accounts/login/?next=" + self._edit_url(section.id))
