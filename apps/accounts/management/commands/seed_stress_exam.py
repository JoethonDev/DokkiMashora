"""Seed data for the Semester Exam stress/load test.

Creates an isolated Diploma -> Cohort -> Semester -> online Semester Exam with a
100-question randomized MCQ bank, plus N active student accounts (default 400)
enrolled in the cohort/semester and ready to take the exam.

This is test-only fixture data. It is isolated by unique titles/usernames so it
never collides with production data, and it is safe to re-run (idempotent).

Usage (inside the web container):
    python manage.py seed_stress_exam --students 400 --questions 100
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import Cohort, Diploma, DiplomaEnrollment, DiplomaLecture, Semester
from apps.accounts.constants import GROUP_STUDENT
from apps.assessments.constants import (
    ASSESSMENT_TYPE_SEMESTER_EXAM,
    GRADE_VISIBILITY_HIDDEN,
    QUESTION_TYPE_MCQ,
    RELEASE_MODE_MANUAL,
)
from apps.assessments.models import (
    Assessment,
    AssessmentAttempt,
    AssessmentLectureSection,
    DiplomaLectureChoice,
    DiplomaLectureQuestion,
)
from apps.students.models import StudentProfile
from apps.assessments.models import (
    AssessmentAttemptQuestion,
    AssessmentAttemptSnapshotAnswer,
)

User = get_user_model()

STRESS_PREFIX = "stress"
DIPLOMA_TITLE = "STRESS Diploma"
COHORT_NAME = "STRESS Cohort 2026"
SEMESTER_TITLE = "STRESS Semester 1"
LECTURE_TITLE = "STRESS Lecture 1"
ASSESSMENT_TITLE = "STRESS Semester Exam (100Q)"
EMAIL_DOMAIN = "dokkimashora.test"
DEFAULT_PASSWORD = "StressLoad123!"


class Command(BaseCommand):
    help = "Seed an isolated 100-question semester exam and N enrolled students for load testing."

    def add_arguments(self, parser):
        parser.add_argument("--students", type=int, default=400, help="Number of student accounts to ensure.")
        parser.add_argument("--questions", type=int, default=100, help="Number of MCQs in the randomized bank.")
        parser.add_argument("--password", type=str, default=DEFAULT_PASSWORD, help="Shared password for stress students.")
        parser.add_argument("--admin-email", type=str, default="loadtest_admin@dokkimashora.test", help="Owning admin email (must not start with 'stress').")
        parser.add_argument(
            "--no-reset",
            action="store_true",
            help="Do not wipe existing STRESS-prefixed data first (additive seed).",
        )

    def handle(self, *args, **options):
        n_students = options["students"]
        n_questions = options["questions"]
        password = options["password"]
        admin_email = options["admin_email"]

        if n_students < 1:
            raise CommandError("--students must be >= 1")
        if n_questions < 1:
            raise CommandError("--questions must be >= 1")

        admin = self._ensure_admin(admin_email, password)

        if not options["no_reset"]:
            self._wipe_stress_data()
            self.stdout.write("  Wiped existing STRESS-prefixed fixtures for a clean slate.")

        with transaction.atomic():
            diploma = Diploma.objects.update_or_create(
                title=DIPLOMA_TITLE,
                defaults={"total_semesters": 1, "is_active": True, "created_by": admin},
            )[0]
            semester = Semester.objects.update_or_create(
                diploma=diploma, order=1, defaults={"title": SEMESTER_TITLE, "is_active": True}
            )[0]
            cohort = Cohort.objects.update_or_create(
                diploma=diploma,
                name=COHORT_NAME,
                defaults={
                    "start_date": timezone.now().date(),
                    "end_date": timezone.now().date().replace(year=timezone.now().year + 1),
                    "is_active": True,
                    "created_by": admin,
                },
            )[0]
            lecture = DiplomaLecture.objects.update_or_create(
                cohort=cohort, semester=semester, order=1, defaults={"title": LECTURE_TITLE}
            )[0]
            assessment = Assessment.objects.update_or_create(
                title=ASSESSMENT_TITLE,
                assessment_type=ASSESSMENT_TYPE_SEMESTER_EXAM,
                cohort=cohort,
                semester=semester,
                defaults={
                    "is_active": True,
                    "open_at": timezone.now() - timezone.timedelta(days=1),
                    "close_at": timezone.now() + timezone.timedelta(days=7),
                    "duration_minutes": 120,
                    "passing_score": Decimal("50"),
                    "max_attempts": 1,
                    "grade_visibility": GRADE_VISIBILITY_HIDDEN,
                    "release_mode": RELEASE_MODE_MANUAL,
                    "penalty_percent": Decimal("0"),
                    "created_by": admin,
                },
            )[0]
            section = AssessmentLectureSection.objects.update_or_create(
                assessment=assessment,
                lecture=lecture,
                order=1,
                defaults={"mcq_count": n_questions, "max_grade": Decimal(n_questions)},
            )[0]
            self._ensure_question_bank(lecture, admin, n_questions)

        created = self._ensure_students(admin, cohort, semester, n_students, password)

        self.stdout.write(self.style.SUCCESS("Stress fixtures ready"))
        self.stdout.write(f"  Diploma     : {diploma.title} (id={diploma.id})")
        self.stdout.write(f"  Cohort      : {cohort.name} (id={cohort.id})")
        self.stdout.write(f"  Semester    : {semester.title} (id={semester.id})")
        self.stdout.write(f"  Assessment  : {assessment.title} (id={assessment.id})  type=semester_exam")
        self.stdout.write(f"  Section     : id={section.id} mcq_count={section.mcq_count}")
        self.stdout.write(f"  Students    : {n_students} (created this run: {created})")
        self.stdout.write(f"  Login       : email=stress0001@{EMAIL_DOMAIN} .. stress{n_students:04d}@{EMAIL_DOMAIN}")
        self.stdout.write(f"  Password    : {password}")
        self.stdout.write("")
        self.stdout.write("Run the load test from the host against nginx (port 80):")
        self.stdout.write(
            f"  python scripts/stress_semester_exam.py --assessment-id {assessment.id} "
            f"--users {n_students} --duration 300"
        )

    def _ensure_admin(self, email, password):
        user, created = User.objects.get_or_create(
            email=email,
            defaults={"full_name": "Stress Admin", "is_active": True, "is_staff": True, "is_superuser": True},
        )
        if created:
            user.set_password(password)
            user.save()
        return user

    def _wipe_stress_data(self):
        """Delete every STRESS-prefixed fixture so the seed is fully reproducible."""
        AssessmentAttemptSnapshotAnswer.objects.filter(
            attempt_question__attempt__assessment__title=ASSESSMENT_TITLE
        ).delete()
        AssessmentAttemptQuestion.objects.filter(attempt__assessment__title=ASSESSMENT_TITLE).delete()
        AssessmentAttempt.objects.filter(assessment__title=ASSESSMENT_TITLE).delete()
        AssessmentLectureSection.objects.filter(assessment__title=ASSESSMENT_TITLE).delete()
        DiplomaLectureChoice.objects.filter(question__lecture__cohort__name=COHORT_NAME).delete()
        DiplomaLectureQuestion.objects.filter(lecture__cohort__name=COHORT_NAME).delete()
        Assessment.objects.filter(title=ASSESSMENT_TITLE).delete()
        DiplomaLecture.objects.filter(cohort__name=COHORT_NAME).delete()
        DiplomaEnrollment.objects.filter(student__user__email__regex=r"^stress\d+@").delete()
        StudentProfile.objects.filter(user__email__regex=r"^stress\d+@").delete()
        # Only numeric stress students (stress0001@...); never the admin account.
        User.objects.filter(email__regex=r"^stress\d+@").delete()
        Semester.objects.filter(diploma__title=DIPLOMA_TITLE).delete()
        Cohort.objects.filter(name=COHORT_NAME).delete()
        Diploma.objects.filter(title=DIPLOMA_TITLE).delete()

    def _ensure_question_bank(self, lecture, admin, n_questions):
        existing = DiplomaLectureQuestion.objects.filter(lecture=lecture, active=True).count()
        if existing >= n_questions:
            self.stdout.write(f"  Question bank already has {existing} active MCQs; skipping creation.")
            return
        to_create = []
        for i in range(n_questions - existing):
            q = DiplomaLectureQuestion(
                lecture=lecture,
                text=f"STRESS MCQ #{existing + i + 1}: select the correct option.",
                question_type=QUESTION_TYPE_MCQ,
                points=Decimal("1.00"),
                active=True,
                created_by=admin,
            )
            to_create.append(q)
        DiplomaLectureQuestion.objects.bulk_create(to_create)

        questions = list(DiplomaLectureQuestion.objects.filter(lecture=lecture, active=True).order_by("id")[:n_questions])
        choices = []
        for q in questions:
            for c in range(4):
                choices.append(
                    DiplomaLectureChoice(
                        question=q,
                        text=f"Option {c + 1}",
                        is_correct=(c == 0),
                        order=c,
                    )
                )
        DiplomaLectureChoice.objects.bulk_create(choices)
        self.stdout.write(f"  Created {len(to_create)} questions and {len(choices)} choices for the bank.")

    def _ensure_students(self, admin, cohort, semester, n_students, password):
        def make_user(i):
            return User(
                email=f"{STRESS_PREFIX}{i:04d}@{EMAIL_DOMAIN}",
                full_name=f"Stress Student {i}",
                is_active=True,
                password=make_password(password),
            )

        def make_profile(user, n):
            return StudentProfile(
                user=user,
                full_name=f"Stress Student {n}",
                date_of_birth=timezone.now().date().replace(year=2000),
                gender="male",
                address="STRESS ADDRESS",
                phone=f"+218{n:09d}",
                national_id=f"STRESS{n:010d}",
                education="bachelor",
            )

        student_group, _ = Group.objects.get_or_create(name=GROUP_STUDENT)

        # _wipe_stress_data() already removed all STRESS users, so this is a clean create.
        users = [make_user(i) for i in range(1, n_students + 1)]
        User.objects.bulk_create(users)

        created_users = list(
            User.objects.filter(email__startswith=f"{STRESS_PREFIX}").order_by("email")[:n_students]
        )

        memberships = [User.groups.through(user_id=u.id, group_id=student_group.id) for u in created_users]
        User.groups.through.objects.bulk_create(memberships, ignore_conflicts=True)

        profiles = [make_profile(u, idx + 1) for idx, u in enumerate(created_users)]
        StudentProfile.objects.bulk_create(profiles)

        created_profiles = list(StudentProfile.objects.filter(user__in=[u.id for u in created_users]))
        enrollments = [
            DiplomaEnrollment(
                student=p,
                cohort=cohort,
                status=ENROLLMENT_STATUS_ACTIVE,
                entry_semester=semester,
            )
            for p in created_profiles
        ]
        DiplomaEnrollment.objects.bulk_create(enrollments)
        self.stdout.write(
            f"  Created {n_students} students + profiles + active diploma enrollments."
        )
        return n_students
