"""Deterministic E2E demo seed - idempotent, safe to re-run.

Run twice in a row and the second run must not create duplicates or print
misleading "+ created" lines. Each *_ensure_* helper is responsible for
keeping the row's current values in sync via update_or_create where the
seed owns the row, and via per-key existence checks for append-only
tables such as AuditEvent.

Demo fixture state is written directly via ORM. This is intentional:
the seed builds a known state for the E2E suite, not a real business
workflow. Business workflow proof lives in the E2E test files.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.academics.constants import (
    ENROLLMENT_STATUS_ACTIVE,
    ENROLLMENT_STATUS_EXPIRED,
    ENROLLMENT_STATUS_PENDING,
    ENROLLMENT_STATUS_RESTRICTED,
)
from apps.academics.models import (
    Cohort,
    Course,
    CourseEnrollment,
    CourseVersion,
    Diploma,
    DiplomaEnrollment,
    Lecture,
    ModeratorStudentAssignment,
    Semester,
)
from apps.applications.constants import (
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_REJECTED,
    PROGRAM_APP_STATUS_SUBMITTED,
    PROGRAM_TYPE_DIPLOMA,
)
from apps.applications.models import ProgramApplication
from apps.assessments.constants import (
    ASSESSMENT_TYPE_HOMEWORK,
    ASSESSMENT_TYPE_QUIZ,
    ASSESSMENT_TYPE_SEMESTER_EXAM,
    ATTEMPT_STATUS_GRADED,
    ATTEMPT_STATUS_UNDER_REVIEW,
    GRADE_VISIBILITY_RELEASED,
    HOMEWORK_STATUS_UNDER_REVIEW,
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
from apps.audit.constants import (
    ACTION_APPLICATION_ACTIVATED,
    ACTION_CERTIFICATE_GENERATED,
    ACTION_GRADE_RELEASED,
    ACTION_PAYMENT_UPLOAD_CONFIRMED,
)
from apps.audit.models import AuditEvent
from apps.certificates.models import CertificateTemplate, GeneratedCertificate
from apps.communications.constants import (
    TEMPLATE_ACCEPTANCE,
    TEMPLATE_ACTIVATION,
    TEMPLATE_ENTRANCE_EXAM_INVITATION,
    TEMPLATE_RECEIVED,
    TEMPLATE_REJECTION,
)
from apps.communications.models import EmailTemplate
from apps.entrance_exams.constants import ATTEMPT_STATUS_ASSIGNED
from apps.entrance_exams.models import (
    EntranceAttempt,
    EntranceChoice,
    EntranceExam,
    EntranceQuestion,
)
from apps.payments.constants import (
    PAYMENT_STATUS_PAID,
    PAYMENT_STATUS_UNPAID,
)
from apps.payments.models import PaymentRecord
from apps.progression.constants import (
    CERTIFICATE_STATUS_AVAILABLE,
    DIPLOMA_COMPLETION_COMPLETED,
)
from apps.progression.models import StudentDiplomaCompletion
from apps.students.constants import GENDER_FEMALE, GENDER_MALE
from apps.students.models import StudentProfile

User = get_user_model()

PASSWORD = "TestPass123!"

# Role names must match the GROUP_* constants in apps/accounts/constants.py.
ROLE_SUPER_ADMIN = "Super Admin"
ROLE_ADMIN = "Admin"
ROLE_MODERATOR = "Moderator"
ROLE_STUDENT = "Student"

# Student definitions.
STUDENTS: list[dict[str, Any]] = [
    {
        "email": "student.active@dokkimashora.test",
        "full_name": "Active Student",
        "phone": "+201100000001",
        "national_id": "29901011234567",
        "country_code": "EG",
        "gender": GENDER_MALE,
        "dob": date(1998, 3, 15),
        "address": "Cairo, Egypt",
        "education": "bachelor",
        "specialization": "Theology",
        "employer": "Church of St. Mark",
        "current_job": "Deacon",
        "church": "St. Mark Coptic Orthodox",
        "priest_name": "Fr. Matta El Meskin",
        "priest_contact": "+201100000099",
    },
    {
        "email": "student.expired@dokkimashora.test",
        "full_name": "Expired Student",
        "phone": "+201100000002",
        "national_id": "29901021234567",
        "country_code": "EG",
        "gender": GENDER_MALE,
        "dob": date(1995, 7, 22),
        "address": "Alexandria, Egypt",
        "education": "master",
        "specialization": "Biblical Studies",
        "employer": "Diocese of Alexandria",
        "current_job": "Teacher",
        "church": "St. George Coptic Orthodox",
        "priest_name": "Fr. Youhanna El Souriani",
        "priest_contact": "+201100000098",
    },
    {
        "email": "student.restricted@dokkimashora.test",
        "full_name": "Restricted Student",
        "phone": "+201100000003",
        "national_id": "29901031234567",
        "country_code": "EG",
        "gender": GENDER_FEMALE,
        "dob": date(2000, 1, 10),
        "address": "Giza, Egypt",
        "education": "bachelor",
        "specialization": "Coptic Studies",
        "employer": "",
        "current_job": "Student",
        "church": "The Holy Virgin Coptic Orthodox",
        "priest_name": "Fr. Boulos Halim",
        "priest_contact": "+201100000097",
    },
    {
        "email": "student.pending@dokkimashora.test",
        "full_name": "Pending Student",
        "phone": "+201100000004",
        "national_id": "29901041234567",
        "country_code": "EG",
        "gender": GENDER_MALE,
        "dob": date(1999, 11, 5),
        "address": "Luxor, Egypt",
        "education": "secondary",
        "specialization": "",
        "employer": "",
        "current_job": "Unemployed",
        "church": "St. Abanoub Coptic Orthodox",
        "priest_name": "Fr. Shenouda Mansour",
        "priest_contact": "+201100000096",
    },
    {
        "email": "student.noaccess@dokkimashora.test",
        "full_name": "No Access Student",
        "phone": "+201100000005",
        "national_id": "29901051234567",
        "country_code": "EG",
        "gender": GENDER_FEMALE,
        "dob": date(1997, 5, 30),
        "address": "Asyut, Egypt",
        "education": "doctorate",
        "specialization": "Patristics",
        "employer": "Monastery of St. Macarius",
        "current_job": "Researcher",
        "church": "St. Macarius Coptic Orthodox",
        "priest_name": "Fr. Athanasius Iskandar",
        "priest_contact": "+201100000095",
    },
    {
        "email": "student.saudi@dokkimashora.test",
        "full_name": "Saudi Student",
        "phone": "+966500000001",
        "national_id": "AB123456",
        "country_code": "SA",
        "gender": GENDER_MALE,
        "dob": date(1996, 8, 20),
        "address": "Riyadh, Saudi Arabia",
        "education": "bachelor",
        "specialization": "Islamic Studies",
        "employer": "",
        "current_job": "Student",
        "church": "",
        "priest_name": "",
        "priest_contact": "",
    },
]


class Command(BaseCommand):
    help = "Seed deterministic E2E demo data. Idempotent - safe to re-run."

    def handle(self, *args, **options):
        self.warnings: list[str] = []
        self.stdout.write("Seeding E2E demo data...")

        # Group cache lives on self so it is built lazily on first use and
        # never touches the DB at module import time.
        self.groups: dict[str, Group] = {}

        with transaction.atomic():
            super_admin = self._ensure_super_admin()
            admin = self._ensure_admin()
            moderator = self._ensure_user("moderator@dokkimashora.test", ROLE_MODERATOR, is_staff=True)

            student_users: dict[str, tuple[User, dict]] = {}
            for s in STUDENTS:
                is_active = s["email"] != "student.pending@dokkimashora.test"
                user = self._ensure_user(s["email"], ROLE_STUDENT, is_active=is_active)
                student_users[s["email"]] = (user, s)

            diploma, sem1, sem2, course, cohort_a, cohort_b = self._ensure_academics(super_admin)
            course_version = self._ensure_course_version(super_admin, course)
            self._ensure_lectures(course_version)

            profiles: dict[str, StudentProfile] = {}
            for email, (user, data) in student_users.items():
                profiles[email] = self._ensure_profile(user, data)

            apps_map = self._ensure_applications(profiles, super_admin, cohort_a, cohort_b, course)
            self._ensure_enrollments(profiles, cohort_a, course_version)
            assessments = self._ensure_assessments(admin, course_version, cohort_a, sem1)
            attempt_for_audit = self._ensure_attempts(assessments, student_users, profiles)
            self._ensure_entrance_exam(diploma, cohort_a, profiles, super_admin)
            self._ensure_moderator_assignment(super_admin, moderator, cohort_a, profiles)
            payment_for_audit = self._ensure_payments(profiles, apps_map)
            self._ensure_communications()
            cert_for_audit = self._ensure_certificates(super_admin, profiles, cohort_a, diploma)
            self._ensure_audit(super_admin, profiles, attempt_for_audit, payment_for_audit, cert_for_audit)

        self._print_summary(profiles, diploma, cohort_a, cohort_b, course, course_version, assessments)
        if self.warnings:
            self.stdout.write(self.style.WARNING("\nWarnings / Skipped:"))
            for w in self.warnings:
                self.stdout.write(self.style.WARNING(f"  - {w}"))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _report(self, label: str, created: bool) -> None:
        # Always pass the actual created flag. Do not print "+ created" for
        # rows that already existed.
        if created:
            self.stdout.write(self.style.SUCCESS(f"  + {label}"))
        else:
            self.stdout.write(f"  . {label} (already exists)")

    def _group(self, name: str) -> Group:
        # Lazy, idempotent group cache. Avoids touching the DB at import time.
        if name not in self.groups:
            group, created = Group.objects.get_or_create(name=name)
            self.groups[name] = group
        return self.groups[name]

    def _ensure_super_admin(self) -> User:
        """Ensure the Super Admin user exists, with a known password."""
        user, created = User.objects.get_or_create(
            email="superadmin@dokkimashora.test",
            defaults={"is_staff": True, "is_superuser": True, "is_active": True},
        )
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.set_password(PASSWORD)
        user.save(update_fields=["is_staff", "is_superuser", "is_active", "password"])
        user.groups.clear()
        for role in (ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_MODERATOR, ROLE_STUDENT):
            user.groups.add(self._group(role))
        self._report("User: superadmin@dokkimashora.test (Super Admin)", created)
        return user

    def _ensure_admin(self) -> User:
        """Ensure a non-superuser Admin exists. The first seed run is what
        guarantees this row exists; subsequent runs only refresh password
        and group membership.
        """
        user, created = User.objects.get_or_create(
            email="admin@dokkimashora.test",
            defaults={"is_staff": True, "is_active": True},
        )
        user.is_staff = True
        user.is_superuser = False
        user.is_active = True
        user.set_password(PASSWORD)
        user.save(update_fields=["is_staff", "is_superuser", "is_active", "password"])
        user.groups.clear()
        user.groups.add(self._group(ROLE_ADMIN))
        self._report("User: admin@dokkimashora.test (Admin)", created)
        return user

    def _ensure_user(
        self, email: str, role: str, *, is_staff: bool = False,
        is_active: bool = True, is_superuser: bool = False,
    ) -> User:
        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                "is_staff": is_staff,
                "is_active": is_active,
                "is_superuser": is_superuser,
            },
        )
        user.set_password(PASSWORD)
        update_fields = ["password"]
        if user.is_staff != is_staff:
            user.is_staff = is_staff
            update_fields.append("is_staff")
        if user.is_superuser != is_superuser:
            user.is_superuser = is_superuser
            update_fields.append("is_superuser")
        if user.is_active != is_active:
            user.is_active = is_active
            update_fields.append("is_active")
        if role == ROLE_STUDENT and not user.country_code:
            user.country_code = "EG"
            update_fields.append("country_code")
        user.save(update_fields=update_fields)
        user.groups.clear()
        user.groups.add(self._group(role))
        self._report(f"User: {email} ({role})", created)
        return user

    def _ensure_profile(self, user: User, data: dict) -> StudentProfile:
        # update_or_create keeps the row's current values in sync so that
        # changes to the seed (e.g. a corrected phone) propagate to the DB.
        profile, created = StudentProfile.objects.update_or_create(
            user=user,
            defaults={
                "full_name": data["full_name"],
                "date_of_birth": data["dob"],
                "gender": data["gender"],
                "address": data["address"],
                "phone": data["phone"],
                "national_id": data["national_id"],
                "country_code": data.get("country_code", "EG"),
                "education": data["education"],
                "specialization": data["specialization"],
                "employer": data["employer"],
                "current_job": data["current_job"],
                "church": data["church"],
                "priest_name": data["priest_name"],
                "priest_contact": data["priest_contact"],
            },
        )
        self._report(f"Profile: {data['full_name']}", created)
        return profile

    def _ensure_academics(self, admin: User):
        diploma, created = Diploma.objects.update_or_create(
            title="E2E Counseling Diploma",
            defaults={"total_semesters": 2, "is_active": True, "created_by": admin},
        )
        self._report("Diploma: E2E Counseling Diploma", created)

        sem1, created = Semester.objects.update_or_create(
            diploma=diploma, order=1,
            defaults={"title": "Semester 1", "is_active": True},
        )
        self._report("Semester 1", created)

        sem2, created = Semester.objects.update_or_create(
            diploma=diploma, order=2,
            defaults={"title": "Semester 2", "is_active": True},
        )
        self._report("Semester 2", created)

        course, created = Course.objects.update_or_create(
            title="E2E Foundations Course",
            defaults={"is_active": True, "created_by": admin},
        )
        self._report("Course: E2E Foundations Course", created)

        cohort_a, created = Cohort.objects.update_or_create(
            diploma=diploma, name="E2E Cohort A 2026",
            defaults={
                "start_date": date(2026, 1, 15),
                "end_date": date(2026, 12, 15),
                "is_active": True,
                "created_by": admin,
            },
        )
        self._report("Cohort A 2026", created)

        cohort_b, created = Cohort.objects.update_or_create(
            diploma=diploma, name="E2E Cohort B 2026",
            defaults={
                "start_date": date(2026, 6, 1),
                "end_date": date(2027, 5, 31),
                "is_active": True,
                "created_by": admin,
            },
        )
        self._report("Cohort B 2026", created)

        return diploma, sem1, sem2, course, cohort_a, cohort_b

    def _ensure_course_version(self, admin, course):
        course_version = (
            CourseVersion.objects
            .filter(course=course, label="course")
            .first()
        )
        if course_version is None:
            course_version = CourseVersion.objects.create(
                course=course,
                label="course",
                access_duration_days=90,
                is_active=True,
                created_by=admin,
            )
            self._report("CourseVersion", True)
        else:
            course_version.access_duration_days = 90
            course_version.is_active = True
            course_version.created_by = admin
            course_version.save()
            self._report("CourseVersion", False)
        return course_version

    def _ensure_lectures(self, ver):
        now = timezone.now()
        _obj, created = Lecture.objects.update_or_create(
            course_version=ver, order=1,
            defaults={
                "title": "E2E Lecture 1 - Published Released",
                "description": "First published and released lecture for E2E testing.",
                "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "release_at": now - timedelta(days=30),
                "is_published": True,
            },
        )
        self._report("Lecture: Published Released", created)
        _obj, created = Lecture.objects.update_or_create(
            course_version=ver, order=2,
            defaults={
                "title": "E2E Lecture 2 - Future Release",
                "description": "Lecture scheduled for future release.",
                "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "release_at": now + timedelta(days=30),
                "is_published": True,
            },
        )
        self._report("Lecture: Future Release", created)
        _obj, created = Lecture.objects.update_or_create(
            course_version=ver, order=3,
            defaults={
                "title": "E2E Lecture 3 - Unpublished",
                "description": "Unpublished lecture for draft testing.",
                "is_published": False,
            },
        )
        self._report("Lecture: Unpublished", created)

    def _ensure_applications(self, profiles, admin, cohort_a, cohort_b, course):
        """Create program applications for the demo students."""
        apps_map: dict[str, ProgramApplication] = {}
        now = timezone.now()

        p = profiles["student.active@dokkimashora.test"]
        app, created = ProgramApplication.objects.update_or_create(
            student=p,
            program_type=PROGRAM_TYPE_DIPLOMA,
            cohort=cohort_a,
            defaults={"status": PROGRAM_APP_STATUS_ACCEPTED, "reviewed_by": admin, "reviewed_at": now},
        )
        self._report("ProgramApplication: active student (accepted)", created)
        apps_map["student.active"] = app

        p = profiles["student.expired@dokkimashora.test"]
        app, created = ProgramApplication.objects.update_or_create(
            student=p,
            program_type=PROGRAM_TYPE_DIPLOMA,
            cohort=cohort_b,
            defaults={"status": PROGRAM_APP_STATUS_ACCEPTED, "reviewed_by": admin, "reviewed_at": now - timedelta(days=200)},
        )
        self._report("ProgramApplication: expired student (accepted)", created)
        apps_map["student.expired"] = app

        p = profiles["student.restricted@dokkimashora.test"]
        app, created = ProgramApplication.objects.update_or_create(
            student=p,
            program_type=PROGRAM_TYPE_DIPLOMA,
            cohort=cohort_a,
            defaults={"status": PROGRAM_APP_STATUS_ACCEPTED, "reviewed_by": admin, "reviewed_at": now},
        )
        self._report("ProgramApplication: restricted student (accepted)", created)
        apps_map["student.restricted"] = app

        p = profiles["student.pending@dokkimashora.test"]
        app, created = ProgramApplication.objects.update_or_create(
            student=p,
            program_type=PROGRAM_TYPE_DIPLOMA,
            cohort=cohort_a,
            defaults={"status": PROGRAM_APP_STATUS_SUBMITTED},
        )
        self._report("ProgramApplication: pending student (submitted)", created)
        apps_map["student.pending"] = app

        p = profiles["student.noaccess@dokkimashora.test"]
        app, created = ProgramApplication.objects.update_or_create(
            student=p,
            program_type=PROGRAM_TYPE_DIPLOMA,
            cohort=cohort_a,
            defaults={"status": PROGRAM_APP_STATUS_REJECTED, "reviewed_by": admin, "rejection_reason": "E2E test: rejected program application", "reviewed_at": now},
        )
        self._report("ProgramApplication: noaccess student (rejected)", created)
        apps_map["student.noaccess"] = app

        return apps_map

    def _ensure_enrollments(self, profiles, cohort_a, course_version):
        now = timezone.now()

        # active student: active diploma + active course.
        p = profiles["student.active@dokkimashora.test"]
        _obj, created = DiplomaEnrollment.objects.update_or_create(
            student=p, cohort=cohort_a,
            defaults={
                "status": ENROLLMENT_STATUS_ACTIVE,
                "activated_at": now,
                "expires_at": now + timedelta(days=365),
            },
        )
        self._report("DiplomaEnrollment: active student", created)

        _obj, created = CourseEnrollment.objects.update_or_create(
            student=p, course_version=course_version,
            defaults={
                "status": ENROLLMENT_STATUS_ACTIVE,
                "activated_at": now,
                "expires_at": now + timedelta(days=180),
            },
        )
        self._report("CourseEnrollment: active student", created)

        # expired student: diploma still active (admin lifetime),
        # CourseEnrollment is expired.
        p = profiles["student.expired@dokkimashora.test"]
        _obj, created = DiplomaEnrollment.objects.update_or_create(
            student=p, cohort=cohort_a,
            defaults={
                "status": ENROLLMENT_STATUS_ACTIVE,
                "activated_at": now - timedelta(days=400),
                "expires_at": now - timedelta(days=35),
            },
        )
        self._report("DiplomaEnrollment: expired student", created)

        _obj, created = CourseEnrollment.objects.update_or_create(
            student=p, course_version=course_version,
            defaults={
                "status": ENROLLMENT_STATUS_EXPIRED,
                "activated_at": now - timedelta(days=200),
                "expires_at": now - timedelta(days=20),
            },
        )
        self._report("CourseEnrollment: expired student", created)

        # restricted student: restricted diploma enrollment.
        p = profiles["student.restricted@dokkimashora.test"]
        _obj, created = DiplomaEnrollment.objects.update_or_create(
            student=p, cohort=cohort_a,
            defaults={
                "status": ENROLLMENT_STATUS_RESTRICTED,
                "activated_at": now,
                "expires_at": now + timedelta(days=365),
            },
        )
        self._report("DiplomaEnrollment: restricted student", created)

        # pending student: pending diploma enrollment.
        p = profiles["student.pending@dokkimashora.test"]
        _obj, created = DiplomaEnrollment.objects.update_or_create(
            student=p, cohort=cohort_a,
            defaults={
                "status": ENROLLMENT_STATUS_PENDING,
            },
        )
        self._report("DiplomaEnrollment: pending student", created)

        # noaccess: no enrollment.

    def _ensure_assessments(self, creator, ver_a, cohort_a, sem1):
        now = timezone.now()
        assessments: dict[str, Assessment] = {}

        # Quiz MCQ (timed, released visibility).
        quiz, created = Assessment.objects.update_or_create(
            title="E2E Quiz MCQ",
            defaults={
                "assessment_type": ASSESSMENT_TYPE_QUIZ,
                "course_version": ver_a,
                "semester": None,
                "cohort": None,
                "created_by": creator,
                "open_at": now - timedelta(days=1),
                "close_at": now + timedelta(days=1),
                "duration_minutes": 30,
                "total_points": 10,
                "passing_score": 50,
                "is_active": True,
                "max_attempts": 1,
                "show_correct_answers": False,
                "grade_visibility": GRADE_VISIBILITY_RELEASED,
            },
        )
        self._report("Assessment: E2E Quiz MCQ", created)
        assessments["quiz_mcq"] = quiz

        q1, created = AssessmentQuestion.objects.update_or_create(
            assessment=quiz, order=1,
            defaults={"text": "What is the capital of France?", "question_type": QUESTION_TYPE_MCQ, "points": 5},
        )
        _obj, created = AssessmentChoice.objects.update_or_create(
            question=q1, text="London", defaults={"is_correct": False, "order": 1},
        )
        _obj, created = AssessmentChoice.objects.update_or_create(
            question=q1, text="Paris", defaults={"is_correct": True, "order": 2},
        )
        _obj, created = AssessmentChoice.objects.update_or_create(
            question=q1, text="Berlin", defaults={"is_correct": False, "order": 3},
        )

        q2, created = AssessmentQuestion.objects.update_or_create(
            assessment=quiz, order=2,
            defaults={"text": "How many days are in a week?", "question_type": QUESTION_TYPE_MCQ, "points": 5},
        )
        _obj, created = AssessmentChoice.objects.update_or_create(
            question=q2, text="5", defaults={"is_correct": False, "order": 1},
        )
        _obj, created = AssessmentChoice.objects.update_or_create(
            question=q2, text="6", defaults={"is_correct": False, "order": 2},
        )
        _obj, created = AssessmentChoice.objects.update_or_create(
            question=q2, text="7", defaults={"is_correct": True, "order": 3},
        )

        # Mixed standalone-course quiz (MCQ + written).
        mixed, created = Assessment.objects.update_or_create(
            title="E2E Mixed Assessment",
            defaults={
                "assessment_type": ASSESSMENT_TYPE_QUIZ,
                "course_version": ver_a,
                "semester": None,
                "cohort": None,
                "created_by": creator,
                "open_at": now - timedelta(days=1),
                "close_at": now + timedelta(days=1),
                "duration_minutes": 45,
                "total_points": 20,
                "passing_score": 50,
                "is_active": True,
                "max_attempts": 1,
                "grade_visibility": GRADE_VISIBILITY_RELEASED,
            },
        )
        self._report("Assessment: E2E Mixed Assessment", created)
        assessments["mixed"] = mixed

        mq1, created = AssessmentQuestion.objects.update_or_create(
            assessment=mixed, order=1,
            defaults={"text": "MCQ: Which is a fruit?", "question_type": QUESTION_TYPE_MCQ, "points": 5},
        )
        _obj, created = AssessmentChoice.objects.update_or_create(
            question=mq1, text="Carrot", defaults={"is_correct": False, "order": 1},
        )
        _obj, created = AssessmentChoice.objects.update_or_create(
            question=mq1, text="Apple", defaults={"is_correct": True, "order": 2},
        )

        mq2, created = AssessmentQuestion.objects.update_or_create(
            assessment=mixed, order=2,
            defaults={"text": "MCQ: Which is a color?", "question_type": QUESTION_TYPE_MCQ, "points": 5},
        )
        _obj, created = AssessmentChoice.objects.update_or_create(
            question=mq2, text="Table", defaults={"is_correct": False, "order": 1},
        )
        _obj, created = AssessmentChoice.objects.update_or_create(
            question=mq2, text="Blue", defaults={"is_correct": True, "order": 2},
        )

        _obj, created = AssessmentQuestion.objects.update_or_create(
            assessment=mixed, order=3,
            defaults={"text": "Written: Describe your understanding of counseling.", "question_type": QUESTION_TYPE_WRITTEN, "points": 5},
        )
        _obj, created = AssessmentQuestion.objects.update_or_create(
            assessment=mixed, order=4,
            defaults={"text": "Written: How would you handle a difficult client?", "question_type": QUESTION_TYPE_WRITTEN, "points": 5},
        )

        # Homework: untimed (duration_minutes=None is the correct value
        # for homework per Assessment.clean()).
        hw, created = Assessment.objects.update_or_create(
            title="E2E Homework Upload",
            defaults={
                "assessment_type": ASSESSMENT_TYPE_HOMEWORK,
                "course_version": None,
                "semester": sem1,
                "cohort": cohort_a,
                "created_by": creator,
                "open_at": now - timedelta(days=1),
                "close_at": now + timedelta(days=7),
                "duration_minutes": None,
                "total_points": 50,
                "passing_score": 50,
                "is_active": True,
                "max_attempts": 1,
            },
        )
        self._report("Assessment: E2E Homework Upload", created)
        assessments["homework"] = hw

        # Semester Exam (timed).
        semester_exam, created = Assessment.objects.update_or_create(
            title="E2E Semester Exam",
            defaults={
                "assessment_type": ASSESSMENT_TYPE_SEMESTER_EXAM,
                "course_version": None,
                "semester": sem1,
                "cohort": cohort_a,
                "created_by": creator,
                "open_at": now - timedelta(days=1),
                "close_at": now + timedelta(days=7),
                "duration_minutes": 120,
                "total_points": 100,
                "passing_score": 60,
                "is_active": True,
                "max_attempts": 1,
                "grade_visibility": GRADE_VISIBILITY_RELEASED,
            },
        )
        self._report("Assessment: E2E Semester Exam", created)
        assessments["semester_exam"] = semester_exam

        return assessments

    def _ensure_attempts(self, assessments, student_users, profiles):
        """Create one graded attempt, one under_review attempt, and one
        homework submission. Returns the graded attempt for the audit
        seed to reference.
        """
        now = timezone.now()
        active_user = student_users["student.active@dokkimashora.test"][0]

        # Graded attempt for Quiz MCQ.
        quiz = assessments["quiz_mcq"]
        attempt, created = AssessmentAttempt.objects.update_or_create(
            assessment=quiz, student=active_user, attempt_number=1,
            defaults={
                "status": ATTEMPT_STATUS_GRADED,
                "started_at": now - timedelta(hours=2),
                "submitted_at": now - timedelta(hours=1),
                "graded_at": now - timedelta(minutes=30),
                "score_points": 8,
                "score_percent": 80,
                "is_pass": True,
                "visible_to_student": True,
            },
        )
        self._report("AssessmentAttempt: quiz MCQ (graded)", created)

        # Under-review attempt for Mixed Assessment.
        mixed = assessments["mixed"]
        _obj, created = AssessmentAttempt.objects.update_or_create(
            assessment=mixed, student=active_user, attempt_number=1,
            defaults={
                "status": ATTEMPT_STATUS_UNDER_REVIEW,
                "started_at": now - timedelta(hours=1),
                "submitted_at": now - timedelta(minutes=30),
            },
        )
        self._report("AssessmentAttempt: mixed (under review)", created)

        # Homework submission.
        hw = assessments["homework"]
        hw_attempt, created = AssessmentAttempt.objects.update_or_create(
            assessment=hw, student=active_user, attempt_number=1,
            defaults={
                "status": ATTEMPT_STATUS_UNDER_REVIEW,
                "started_at": now - timedelta(days=2),
                "submitted_at": now - timedelta(days=1),
            },
        )
        _obj, created = HomeworkSubmission.objects.update_or_create(
            assessment=hw, student=active_user,
            defaults={
                "status": HOMEWORK_STATUS_UNDER_REVIEW,
                "attempt": hw_attempt,
                "submitted_at": now - timedelta(days=1),
            },
        )
        self._report("HomeworkSubmission: e2e homework", created)

        return attempt

    def _ensure_entrance_exam(self, diploma, cohort_a, profiles, admin):
        now = timezone.now()
        exam, created = EntranceExam.objects.update_or_create(
            diploma=diploma, cohort=cohort_a,
            defaults={
                "title": "E2E Entrance Exam Cohort A",
                "duration_minutes": 30,
                "pass_percentage": 60,
                "total_score": 30,
                "is_active": True,
                "open_at": now - timedelta(days=1),
                "close_at": now + timedelta(days=1),
                "created_by": admin,
            },
        )
        self._report("EntranceExam: E2E Cohort A", created)

        eq1, created = EntranceQuestion.objects.update_or_create(
            exam=exam, order=1,
            defaults={"text": "MCQ: What is theology?", "question_type": "mcq"},
        )
        _obj, created = EntranceChoice.objects.update_or_create(
            question=eq1, order=1, defaults={"text": "Study of God", "score": 2},
        )
        _obj, created = EntranceChoice.objects.update_or_create(
            question=eq1, order=2, defaults={"text": "Study of animals", "score": 0},
        )
        _obj, created = EntranceChoice.objects.update_or_create(
            question=eq1, order=3, defaults={"text": "Study of rocks", "score": 0},
        )

        eq2, created = EntranceQuestion.objects.update_or_create(
            exam=exam, order=2,
            defaults={"text": "MCQ: Who wrote the Gospel of John?", "question_type": "mcq"},
        )
        _obj, created = EntranceChoice.objects.update_or_create(
            question=eq2, order=1, defaults={"text": "Matthew", "score": 0},
        )
        _obj, created = EntranceChoice.objects.update_or_create(
            question=eq2, order=2, defaults={"text": "John", "score": 2},
        )
        _obj, created = EntranceChoice.objects.update_or_create(
            question=eq2, order=3, defaults={"text": "Paul", "score": 0},
        )

        _obj, created = EntranceQuestion.objects.update_or_create(
            exam=exam, order=3,
            defaults={"text": "Written: Why do you want to study counseling?", "question_type": "written"},
        )

        # Entrance attempt for pending student.
        p = profiles["student.pending@dokkimashora.test"]
        pa = ProgramApplication.objects.filter(
            student=p, program_type=PROGRAM_TYPE_DIPLOMA, cohort=cohort_a,
        ).first()
        if pa:
            _obj, created = EntranceAttempt.objects.update_or_create(
                program_application=pa, exam=exam,
                defaults={
                    "student": p,
                    "status": ATTEMPT_STATUS_ASSIGNED,
                },
            )
            self._report("EntranceAttempt: pending student (assigned)", created)

    def _ensure_moderator_assignment(self, admin, moderator, cohort_a, profiles):
        p = profiles["student.active@dokkimashora.test"]
        _obj, created = ModeratorStudentAssignment.objects.update_or_create(
            moderator=moderator, student=p, cohort=cohort_a,
            defaults={"active": True, "assigned_by": admin},
        )
        self._report("ModeratorStudentAssignment: moderator -> active student", created)

    def _ensure_payments(self, profiles, apps_map):
        # Paid payment for active student.
        p = profiles["student.active@dokkimashora.test"]
        root_app = apps_map.get("student.active")
        payment, created = PaymentRecord.objects.update_or_create(
            student=p, external_reference="E2E-TXN-001",
            defaults={
                "amount": 1500,
                "currency": "EGP",
                "status": PAYMENT_STATUS_PAID,
                "paid_at": timezone.now() - timedelta(days=30),
                "application": root_app,
            },
        )
        self._report("PaymentRecord: active student (paid)", created)

        # Unpaid payment for pending student.
        p = profiles["student.pending@dokkimashora.test"]
        root_app = apps_map.get("student.pending")
        _obj, created = PaymentRecord.objects.update_or_create(
            student=p, external_reference="E2E-TXN-002",
            defaults={
                "amount": 1500,
                "currency": "EGP",
                "status": PAYMENT_STATUS_UNPAID,
                "due_at": timezone.now() + timedelta(days=14),
                "application": root_app,
            },
        )
        self._report("PaymentRecord: pending student (unpaid)", created)
        return payment

    def _ensure_communications(self):
        templates = [
            (TEMPLATE_RECEIVED, "Registration Received", "Your registration has been received."),
            (TEMPLATE_ACCEPTANCE, "Acceptance", "Congratulations! You have been accepted."),
            (TEMPLATE_REJECTION, "Rejection", "We regret to inform you..."),
            (
                TEMPLATE_ACTIVATION,
                "Activate your account",
                "Hello {{ full_name }},\n\n"
                "Your account has been created. Your login email is {{ email }} "
                "and your initial password is {{ password }}.\n\n"
                "Please activate your account using the link below:\n\n"
                "{{ activation_url }}\n\n"
                "You can change your password after logging in.\n\n"
                "Welcome aboard!\nDokkiMashora admissions",
            ),
            (TEMPLATE_ENTRANCE_EXAM_INVITATION, "Entrance Exam Invitation", "You have been invited to the entrance exam."),
        ]
        for slug, name, body in templates:
            _obj, created = EmailTemplate.objects.update_or_create(
                slug=slug,
                defaults={"name": name, "subject": name, "body": body, "is_active": True},
            )
            self._report(f"EmailTemplate: {slug}", created)

    def _ensure_certificates(self, admin, profiles, cohort_a, diploma):
        cert_template, created = CertificateTemplate.objects.update_or_create(
            diploma=diploma, language="ar", template_type="completion",
            defaults={
                "name": "E2E Counseling Diploma Certificate",
                "active": True,
                "is_placeholder": False,
                "created_by": admin,
            },
        )
        self._report("CertificateTemplate: E2E Counseling Diploma", created)

        p = profiles["student.active@dokkimashora.test"]
        diploma_enr = DiplomaEnrollment.objects.filter(
            student=p, cohort=cohort_a,
        ).first()

        if not diploma_enr:
            self.warnings.append("GeneratedCertificate skipped: no DiplomaEnrollment for active student")
            return None

        # Demo fixture state: completion + certificate are written
        # directly to match what the progression service would produce
        # after a real graduation flow. This is a known state, not a
        # business workflow proof.
        _obj, created = StudentDiplomaCompletion.objects.update_or_create(
            student=p, diploma_enrollment=diploma_enr,
            defaults={
                "status": DIPLOMA_COMPLETION_COMPLETED,
                "completed_at": timezone.now(),
                "final_score": 88,
                "completed_by": admin,
            },
        )
        self._report("StudentDiplomaCompletion: active student", created)

        cert, created = GeneratedCertificate.objects.update_or_create(
            student=p, diploma_enrollment=diploma_enr,
            defaults={
                "certificate_template": cert_template,
                "certificate_number": "E2E-2026-0001",
                "status": CERTIFICATE_STATUS_AVAILABLE,
                "generated_by": admin,
            },
        )
        self._report("GeneratedCertificate: active student", created)
        return cert

    def _ensure_audit(self, admin, profiles, attempt, payment, certificate):
        """Seed a deterministic set of demo audit events.

        AuditEvent is append-only (save() and delete() raise PermissionError
        in the model), so the natural key here is the unique
        (actor, action, target_type, target_id, metadata.scope) tuple. We
        check existence for each event before creating, so re-runs do
        not duplicate.
        """
        p = profiles["student.active@dokkimashora.test"]
        scope = "e2e_demo"

        # 1. Application activated (uses the real ProgramApplication pk).
        application_pk = ProgramApplication.objects.filter(student=p).values_list("pk", flat=True).first()
        if application_pk is not None and not AuditEvent.objects.filter(
            actor=admin, action=ACTION_APPLICATION_ACTIVATED,
            target_type="program_application", target_id=str(application_pk),
            metadata__scope=scope,
        ).exists():
            AuditEvent.objects.create(
                actor=admin, action=ACTION_APPLICATION_ACTIVATED,
                target_type="program_application", target_id=str(application_pk),
                metadata={"email": p.user.email, "scope": scope},
                ip_address="127.0.0.1",
            )
            self._report("Audit: application.activated", True)
        else:
            self._report("Audit: application.activated (already exists)", False)

        # 2. Grade released (uses the real AssessmentAttempt pk).
        if attempt is not None and not AuditEvent.objects.filter(
            actor=admin, action=ACTION_GRADE_RELEASED,
            target_type="assessment_attempt", target_id=str(attempt.pk),
            metadata__scope=scope,
        ).exists():
            AuditEvent.objects.create(
                actor=admin, action=ACTION_GRADE_RELEASED,
                target_type="assessment_attempt", target_id=str(attempt.pk),
                metadata={"assessment_id": attempt.assessment_id, "scope": scope},
                ip_address="127.0.0.1",
            )
            self._report("Audit: grade.released", True)
        else:
            self._report("Audit: grade.released (already exists)", False)

        # 3. Payment upload confirmed (uses the real PaymentRecord pk).
        if payment is not None and not AuditEvent.objects.filter(
            actor=admin, action=ACTION_PAYMENT_UPLOAD_CONFIRMED,
            target_type="payment_record", target_id=str(payment.pk),
            metadata__scope=scope,
        ).exists():
            AuditEvent.objects.create(
                actor=admin, action=ACTION_PAYMENT_UPLOAD_CONFIRMED,
                target_type="payment_record", target_id=str(payment.pk),
                metadata={"external_reference": payment.external_reference, "scope": scope},
                ip_address="127.0.0.1",
            )
            self._report("Audit: payment.upload_confirmed", True)
        else:
            self._report("Audit: payment.upload_confirmed (already exists)", False)

        # 4. Certificate generated (uses the real GeneratedCertificate pk).
        if certificate is not None and not AuditEvent.objects.filter(
            actor=admin, action=ACTION_CERTIFICATE_GENERATED,
            target_type="generated_certificate", target_id=str(certificate.pk),
            metadata__scope=scope,
        ).exists():
            AuditEvent.objects.create(
                actor=admin, action=ACTION_CERTIFICATE_GENERATED,
                target_type="generated_certificate", target_id=str(certificate.pk),
                metadata={"certificate_number": certificate.certificate_number, "scope": scope},
                ip_address="127.0.0.1",
            )
            self._report("Audit: certificate.generated", True)
        else:
            self._report("Audit: certificate.generated (already exists)", False)

    def _print_summary(self, profiles, diploma, cohort_a, cohort_b, course, course_version, assessments):
        creds = [
            ("Super Admin", "superadmin@dokkimashora.test"),
            ("Admin", "admin@dokkimashora.test"),
            ("Moderator", "moderator@dokkimashora.test"),
            ("Student (active)", "student.active@dokkimashora.test"),
            ("Student (expired)", "student.expired@dokkimashora.test"),
            ("Student (restricted)", "student.restricted@dokkimashora.test"),
            ("Student (pending)", "student.pending@dokkimashora.test"),
            ("Student (noaccess)", "student.noaccess@dokkimashora.test"),
        ]

        out: list[str] = []
        out.append("")
        out.append("# E2E Demo Seed Complete")
        out.append("")
        out.append("## Credentials")
        out.append("")
        out.append("| Role | Email | Password | Notes |")
        out.append("|---|---|---|---|")
        for role, email in creds:
            note = ""
            if "pending" in email:
                note = "is_active=False"
            elif "noaccess" in email:
                note = "application rejected"
            out.append(f"| {role} | `{email}` | `{PASSWORD}` | {note} |")

        out.append("")
        out.append("## Important URLs")
        out.append("")
        out.append("- Login: `/accounts/login/`")
        out.append("- Student portal: `/profile/`")
        out.append("- Admin applications: `/applications/admin/`")
        out.append("- Program applications: `/applications/programs/`")
        out.append("- Learning: `/profile/diploma-course/`")
        out.append("- Assessments: `/assessments/`")
        out.append("- Entrance exams: `/entrance-exams/`")
        out.append("- Payments: `/payments/`")
        out.append("- Certificates: `/certificates/`")

        out.append("")
        out.append("## Seeded Object IDs")
        out.append("")
        out.append(f"- Diploma: `{diploma.pk}`")
        out.append(f"- Cohort A: `{cohort_a.pk}`")
        out.append(f"- Cohort B: `{cohort_b.pk}`")
        out.append(f"- Course: `{course.pk}`")
        out.append(f"- Course Version: `{course_version.pk}`")
        for name, a in assessments.items():
            out.append(f"- Assessment ({name}): `{a.pk}`")

        out.append("")
        out.append("## Scenario Mapping")
        out.append("")
        out.append("- **active student**: content access, assessment, homework, certificates")
        out.append("- **expired student**: expired access behavior")
        out.append("- **restricted student**: restricted access behavior")
        out.append("- **pending student**: entrance exam and admin approval")
        out.append("- **noaccess student**: permission denial")

        self.stdout.write("\n".join(out))
