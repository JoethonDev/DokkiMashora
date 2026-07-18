"""Seed deterministic term-access and exam-load QA scenarios."""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.constants import GROUP_ADMIN, GROUP_STUDENT
from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import (
    Cohort,
    Course,
    CourseEnrollment,
    CourseVersion,
    Diploma,
    DiplomaEnrollment,
    Semester,
)
from apps.assessments.constants import ASSESSMENT_TYPE_QUIZ, QUESTION_TYPE_MCQ
from apps.assessments.models import Assessment, AssessmentAttempt, AssessmentChoice, AssessmentQuestion
from apps.progression.constants import (
    PROMOTION_DECISION_ACADEMIC_YEAR_PROMOTE,
    SEMESTER_PROGRESS_FAILED,
    SEMESTER_PROGRESS_IN_PROGRESS,
    SEMESTER_PROGRESS_PASSED,
    SEMESTER_PROGRESS_PROMOTED,
)
from apps.progression.models import CohortAcademicYear, PromotionDecision, StudentSemesterProgress
from apps.students.constants import GENDER_MALE
from apps.students.models import StudentProfile

User = get_user_model()
PASSWORD = "TestPass123!"

TERM_STATES = {
    "first": {},
    "failed": {1: SEMESTER_PROGRESS_FAILED},
    "passed": {1: SEMESTER_PROGRESS_PASSED},
    "year-blocked": {1: SEMESTER_PROGRESS_PASSED, 2: SEMESTER_PROGRESS_PASSED},
    "year-open": {1: SEMESTER_PROGRESS_PASSED, 2: SEMESTER_PROGRESS_PASSED},
}


class Command(BaseCommand):
    help = "Seed named term-access states and concurrent exam-load users"

    def add_arguments(self, parser):
        parser.add_argument("--load-students", type=int, default=10)

    @transaction.atomic
    def handle(self, *args, **options):
        load_students = options["load_students"]
        if load_students < 1:
            raise ValueError("--load-students must be at least 1")

        admin = self._user(
            "qa.admin@dokkimashora.test", "QA Admin", 0, GROUP_ADMIN, staff=True
        )
        diploma, cohort, semesters = self._term_structure(admin)
        self._term_students(cohort, semesters)
        assessment = self._load_assessment(admin, load_students)

        self.stdout.write(self.style.SUCCESS("QA scenarios are ready."))
        self.stdout.write(f"Password for every QA user: {PASSWORD}")
        self.stdout.write(
            "Term users: "
            + ", ".join(f"qa.term.{state}@dokkimashora.test" for state in TERM_STATES)
        )
        self.stdout.write(f"Term diploma: {diploma.title} (cohort {cohort.pk})")
        self.stdout.write(f"Load users: load.student.0001..{load_students:04d}@dokkimashora.test")
        self.stdout.write(f"Load assessment ID: {assessment.pk}")

    def _user(self, email, full_name, number, group_name, *, staff=False):
        user, _ = User.objects.update_or_create(
            email=email,
            defaults={
                "full_name": full_name,
                "phone": f"+2099{number:010d}",
                "national_id": str(90000000000000 + number),
                "is_active": True,
                "is_staff": staff,
            },
        )
        user.set_password(PASSWORD)
        user.save(update_fields=["password"])
        user.groups.set([Group.objects.get_or_create(name=group_name)[0]])
        return user

    def _profile(self, user):
        profile, _ = StudentProfile.objects.update_or_create(
            user=user,
            defaults={
                "full_name": user.full_name,
                "date_of_birth": date(1995, 1, 1),
                "gender": GENDER_MALE,
                "address": "QA",
                "phone": user.phone,
                "country_code": "EG",
                "national_id": user.national_id,
                "education": "bachelor",
            },
        )
        return profile

    def _term_structure(self, admin):
        diploma, _ = Diploma.objects.update_or_create(
            title="QA Term Access Diploma",
            defaults={"total_semesters": 4, "is_active": True, "created_by": admin},
        )
        semesters = {}
        for order in range(1, 5):
            semesters[order], _ = Semester.objects.update_or_create(
                diploma=diploma,
                order=order,
                defaults={"title": f"QA Semester {order}", "is_active": True},
            )
        cohort, _ = Cohort.objects.update_or_create(
            diploma=diploma,
            name="QA Term Cohort",
            defaults={
                "start_date": date(2026, 1, 1),
                "end_date": date(2027, 12, 31),
                "is_active": True,
                "created_by": admin,
            },
        )
        for year, start in ((1, date(2026, 1, 1)), (2, date(2027, 1, 1))):
            CohortAcademicYear.objects.update_or_create(
                cohort=cohort,
                year_number=year,
                defaults={
                    "start_date": start,
                    "end_date": date(start.year, 12, 31),
                    "semesters_per_year": 2,
                },
            )
        return diploma, cohort, semesters

    def _term_students(self, cohort, semesters):
        for index, (state, statuses) in enumerate(TERM_STATES.items(), start=1):
            user = self._user(
                f"qa.term.{state}@dokkimashora.test",
                f"QA Term {state.title()}",
                100 + index,
                GROUP_STUDENT,
            )
            profile = self._profile(user)
            enrollment, _ = DiplomaEnrollment.objects.update_or_create(
                student=profile,
                cohort=cohort,
                defaults={"status": ENROLLMENT_STATUS_ACTIVE, "activated_at": timezone.now()},
            )
            enrollment.semester_progress.all().delete()
            enrollment.promotion_decisions.all().delete()
            for order, status in statuses.items():
                StudentSemesterProgress.objects.create(
                    student=profile,
                    diploma_enrollment=enrollment,
                    cohort=cohort,
                    semester=semesters[order],
                    status=status,
                )
            if state == "year-open":
                PromotionDecision.objects.create(
                    student=profile,
                    diploma_enrollment=enrollment,
                    from_semester=semesters[2],
                    to_semester=semesters[3],
                    decision_type=PROMOTION_DECISION_ACADEMIC_YEAR_PROMOTE,
                    previous_status=SEMESTER_PROGRESS_PASSED,
                    new_status=SEMESTER_PROGRESS_PROMOTED,
                    reason="QA academic-year promotion",
                )
                StudentSemesterProgress.objects.create(
                    student=profile,
                    diploma_enrollment=enrollment,
                    cohort=cohort,
                    semester=semesters[3],
                    status=SEMESTER_PROGRESS_IN_PROGRESS,
                )

    def _load_assessment(self, admin, count):
        course, _ = Course.objects.update_or_create(
            title="QA Load Test Course",
            defaults={"is_active": True, "created_by": admin},
        )
        version, _ = CourseVersion.objects.update_or_create(
            course=course,
            label="load-v1",
            defaults={
                "access_duration_days": 30,
                "is_active": True,
                "created_by": admin,
            },
        )
        now = timezone.now()
        assessment, _ = Assessment.objects.update_or_create(
            title="QA Concurrent Exam",
            course_version=version,
            defaults={
                "description": "Owned by seed_qa_scenarios.",
                "assessment_type": ASSESSMENT_TYPE_QUIZ,
                "cohort": None,
                "semester": None,
                "open_at": now - timedelta(days=1),
                "close_at": now + timedelta(days=30),
                "duration_minutes": 30,
                "total_points": Decimal("1.00"),
                "passing_score": Decimal("0.00"),
                "max_attempts": 100000,
                "is_active": True,
                "created_by": admin,
            },
        )
        question, _ = AssessmentQuestion.objects.update_or_create(
            assessment=assessment,
            order=1,
            defaults={
                "text": "QA load question",
                "question_type": QUESTION_TYPE_MCQ,
                "points": Decimal("1.00"),
                "required": True,
            },
        )
        AssessmentChoice.objects.update_or_create(
            question=question,
            order=1,
            defaults={"text": "Correct", "is_correct": True},
        )
        AssessmentChoice.objects.update_or_create(
            question=question,
            order=2,
            defaults={"text": "Incorrect", "is_correct": False},
        )
        AssessmentAttempt.objects.filter(
            assessment=assessment,
            student__email__startswith="load.student.",
        ).delete()
        for index in range(1, count + 1):
            user = self._user(
                f"load.student.{index:04d}@dokkimashora.test",
                f"Load Student {index:04d}",
                1000 + index,
                GROUP_STUDENT,
            )
            profile = self._profile(user)
            CourseEnrollment.objects.update_or_create(
                student=profile,
                course_version=version,
                defaults={"status": ENROLLMENT_STATUS_ACTIVE, "activated_at": now},
            )
        return assessment
