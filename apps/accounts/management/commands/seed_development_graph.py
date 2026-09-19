"""Seed a bounded development/staging graph with deterministic identities.

The command composes ``seed_e2e_demo`` for its existing scenario catalogue,
then adds a parameterized two-cohort population and exercises canonical
services for activation, assessment, attendance, entrance-exam, grading, and
progression transitions. Opaque security tokens remain service-generated.

Deliberately unsupported: real object-storage uploads, media transcoding,
outgoing email delivery, and rendered certificate PDFs. Creating fake binary
objects would bypass the storage invariants; the command reports these gaps.
"""

from __future__ import annotations

import io
import os
import random
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.academics.constants import (
    ENROLLMENT_STATUS_ACTIVE,
    ENROLLMENT_STATUS_EXPIRED,
    ENROLLMENT_STATUS_RESTRICTED,
)
from apps.academics.models import (
    Cohort,
    Course,
    CourseEnrollment,
    CourseVersion,
    Diploma,
    DiplomaEnrollment,
    DiplomaLecture,
    Lecture,
    ModeratorAssignment,
    Semester,
    StudentCluster,
    StudentClusterMembership,
)
from apps.academics.services import activate_course_enrollment
from apps.accounts.constants import (
    GROUP_ADMIN,
    GROUP_MODERATOR,
    GROUP_REVIEWER,
    GROUP_SECRETARY,
    GROUP_STUDENT,
    GROUP_SUPER_ADMIN,
)
from apps.applications.constants import (
    DELIVERY_MODE_OFFLINE,
    DELIVERY_MODE_ONLINE,
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_ACTIVATED,
    PROGRAM_APP_STATUS_INVITED,
    PROGRAM_APP_STATUS_READY_FOR_ACTIVATION,
    PROGRAM_APP_STATUS_REJECTED,
    PROGRAM_APP_STATUS_SUBMITTED,
    PROGRAM_TYPE_DIPLOMA,
)
from apps.applications.models import ProgramApplication
from apps.applications.services import activate_application_if_ready
from apps.assessments.constants import (
    ASSESSMENT_TYPE_HOMEWORK,
    ASSESSMENT_TYPE_QUIZ,
    ASSESSMENT_TYPE_SEMESTER_EXAM,
    ATTEMPT_STATUS_GRADED,
    GRADE_VISIBILITY_HIDDEN,
    HOMEWORK_STATUS_UPLOADED,
    QUESTION_TYPE_MCQ,
    QUESTION_TYPE_WRITTEN,
    RELEASE_MODE_AUTOMATIC,
    RELEASE_MODE_MANUAL,
    REVIEWER_ACCESS_BOTH,
    REVIEWER_SCOPE_EXAM,
)
from apps.assessments.models import (
    Assessment,
    AssessmentChoice,
    AssessmentLectureSection,
    AssessmentQuestion,
    AssessmentSectionManualQuestion,
    DiplomaLectureChoice,
    DiplomaLectureQuestion,
    HomeworkSubmission,
    ReviewerAssignment,
    SemesterExamCode,
)
from apps.assessments.services import (
    AssessmentAnswerData,
    admin_review_semester_exam,
    generate_exam_codes,
    grade_diploma_snapshot_answer,
    release_attempt_grades,
    start_assessment,
    submit_assessment,
    submit_diploma_assessment,
    validate_sections_ready,
)
from apps.attendance.constants import SESSION_STATUS_CLOSED, SESSION_STATUS_OPEN
from apps.attendance.models import AttendancePeriod
from apps.attendance.services import (
    build_attendance_qr_payload,
    close_attendance_session,
    create_attendance_day,
    get_or_create_attendance_code_for_enrollment,
    scan_attendance_token,
)
from apps.audit.models import AuditEvent
from apps.certificates.models import CertificateTemplate, GeneratedCertificate
from apps.communications.constants import (
    EMAIL_STATUS_FAILED,
    EMAIL_STATUS_QUEUED,
    EMAIL_STATUS_SENT,
)
from apps.communications.email_message import EmailMessage
from apps.entrance_exams.constants import (
    ATTEMPT_STATUS_ASSIGNED as ENTRANCE_ASSIGNED,
)
from apps.entrance_exams.constants import (
    ATTEMPT_STATUS_IN_PROGRESS as ENTRANCE_IN_PROGRESS,
)
from apps.entrance_exams.models import EntranceAttempt, EntranceChoice, EntranceExam, EntranceQuestion
from apps.entrance_exams.services import (
    AnswerData as EntranceAnswerData,
)
from apps.entrance_exams.services import (
    assign_attempt,
)
from apps.entrance_exams.services import (
    grade_answer as grade_entrance_answer,
)
from apps.entrance_exams.services import (
    start_attempt as start_entrance_attempt,
)
from apps.entrance_exams.services import (
    submit_attempt as submit_entrance_attempt,
)
from apps.payments.constants import PAYMENT_STATUS_PAID, PAYMENT_STATUS_UNPAID
from apps.payments.models import PaymentRecord
from apps.progression.constants import (
    CERTIFICATE_STATUS_FAILED,
    EVALUATION_CONFIG_STATUS_ACTIVE,
    SEMESTER_EVALUATION_PASS,
)
from apps.progression.models import SemesterEvaluationConfig
from apps.progression.services import finalize_semester_evaluation, resolve_final_progression
from apps.students.constants import EDUCATION_BACHELOR, GENDER_FEMALE, GENDER_MALE
from apps.students.models import StudentProfile

User = get_user_model()
SEED_SCOPE = "development_graph_v1"
BASE_SEED_EMAILS = (
    "superadmin@dokkimashora.test",
    "admin@dokkimashora.test",
    "moderator@dokkimashora.test",
    "student.active@dokkimashora.test",
    "student.expired@dokkimashora.test",
    "student.restricted@dokkimashora.test",
    "student.pending@dokkimashora.test",
    "student.noaccess@dokkimashora.test",
    "student.saudi@dokkimashora.test",
)


class Command(BaseCommand):
    help = (
        "Seed a guarded, idempotent development/staging graph with two cohorts "
        "and a bounded student population."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--students-per-cohort",
            type=int,
            default=200,
            help="Target identities/applications per cohort (1-250; default: 200).",
        )
        parser.add_argument(
            "--smoke",
            action="store_true",
            help="Use 8 students per cohort, ignoring --students-per-cohort.",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Required acknowledgement that seed data may be written.",
        )
        parser.add_argument(
            "--password",
            default=os.environ.get("DOKKI_DEVELOPMENT_SEED_PASSWORD", ""),
            help=(
                "Optional shared login password. Prefer the "
                "DOKKI_DEVELOPMENT_SEED_PASSWORD environment variable. "
                "Without it, new seed accounts cannot log in."
            ),
        )

    def handle(self, *args, **options):
        if getattr(settings, "PRODUCTION_DEPLOYMENT", False):
            raise CommandError("Refusing to seed: PRODUCTION_DEPLOYMENT is enabled.")
        if not options["confirm"]:
            raise CommandError("Refusing to seed without the explicit --confirm flag.")

        count = 8 if options["smoke"] else options["students_per_cohort"]
        if not 1 <= count <= 250:
            raise CommandError("--students-per-cohort must be between 1 and 250.")
        password = options["password"].strip()
        random.seed(20260828)

        with transaction.atomic():
            self._compose_existing_seed(password)
            staff = self._ensure_staff(password)
            graph = self._ensure_academic_graph(staff[GROUP_SUPER_ADMIN])
            populations = {}
            for cohort_number, cohort in enumerate(graph["cohorts"], start=1):
                populations[cohort.pk] = self._ensure_population(
                    cohort=cohort,
                    cohort_number=cohort_number,
                    count=count,
                    course_version=graph["course_version"],
                    admin=staff[GROUP_ADMIN],
                    password=password,
                )
            self._ensure_assignments(graph, populations, staff)
            assessments = self._ensure_assessments(graph, staff[GROUP_ADMIN])
            self._ensure_exam_codes(assessments, staff[GROUP_ADMIN])
            self._ensure_entrance_scenarios(graph, populations, staff[GROUP_ADMIN])
            self._ensure_learning_scenarios(
                graph,
                populations,
                assessments,
                staff,
            )
            self._ensure_messages(populations, staff[GROUP_ADMIN])
            self._ensure_seed_audit(staff[GROUP_ADMIN], count)

        self.stdout.write(self.style.SUCCESS(
            f"Seeded two active cohorts with {count} deterministic students/applications each."
        ))
        self.stdout.write(
            "Login: supplied password applied to seed accounts."
            if password
            else "Login: seed accounts have unusable passwords; rerun with --password or set "
            "DOKKI_DEVELOPMENT_SEED_PASSWORD to enable login."
        )
        self.stdout.write(self.style.WARNING(
            "Not seeded by design: real media/homework binaries, storage-backed certificate PDFs, "
            "outgoing email delivery, and external notifications (no notification model exists)."
        ))

    def _compose_existing_seed(self, password: str) -> None:
        captured = io.StringIO()
        call_command("seed_e2e_demo", stdout=captured, stderr=captured)
        for user in User.objects.filter(email__in=BASE_SEED_EMAILS):
            if password:
                user.set_password(password)
            else:
                user.set_unusable_password()
            user.save(update_fields=["password"])

    def _ensure_staff(self, password: str) -> dict[str, User]:
        definitions = {
            GROUP_SUPER_ADMIN: ("superadmin@dokkimashora.test", True),
            GROUP_ADMIN: ("admin@dokkimashora.test", False),
            GROUP_MODERATOR: ("seed.moderator@dokkimashora.test", False),
            GROUP_REVIEWER: ("seed.reviewer@dokkimashora.test", False),
            GROUP_SECRETARY: ("seed.secretary@dokkimashora.test", False),
        }
        users = {}
        for role, (email, is_superuser) in definitions.items():
            group, _ = Group.objects.get_or_create(name=role)
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    "full_name": f"Seed {role}",
                    "is_active": True,
                    "is_staff": True,
                    "is_superuser": is_superuser,
                    "email_activated_at": timezone.now(),
                },
            )
            user.is_active = True
            user.is_staff = True
            user.is_superuser = is_superuser
            user.email_activated_at = user.email_activated_at or timezone.now()
            if password:
                user.set_password(password)
            elif created:
                user.set_unusable_password()
            user.save()
            user.groups.add(group)
            users[role] = user
        return users

    def _ensure_academic_graph(self, admin: User) -> dict:
        diploma, _ = Diploma.objects.update_or_create(
            title="E2E Counseling Diploma",
            defaults={
                "description": "Deterministic development seed diploma.",
                "total_semesters": 2,
                "is_active": True,
                "created_by": admin,
            },
        )
        semesters = []
        for order in (1, 2):
            semester, _ = Semester.objects.update_or_create(
                diploma=diploma,
                order=order,
                defaults={"title": f"Semester {order}", "is_active": True},
            )
            semesters.append(semester)

        cohort_specs = (
            ("E2E Cohort A 2026", date(2026, 1, 15), date(2027, 6, 30)),
            ("E2E Cohort B 2026", date(2026, 7, 1), date(2027, 12, 31)),
        )
        cohorts = []
        for name, start_date, end_date in cohort_specs:
            cohort, _ = Cohort.objects.update_or_create(
                diploma=diploma,
                name=name,
                defaults={
                    "start_date": start_date,
                    "end_date": end_date,
                    "is_active": True,
                    "created_by": admin,
                },
            )
            cohorts.append(cohort)

        course, _ = Course.objects.update_or_create(
            title="E2E Foundations Course",
            defaults={
                "description": "Standalone course for development seed data.",
                "is_active": True,
                "created_by": admin,
            },
        )
        course_version, _ = CourseVersion.objects.update_or_create(
            course=course,
            label="course",
            defaults={
                "access_duration_days": 3650,
                "is_active": True,
                "created_by": admin,
            },
        )
        for order in range(1, 4):
            Lecture.objects.update_or_create(
                course_version=course_version,
                order=order,
                defaults={
                    "title": f"Seed Course Lecture {order}",
                    "description": "Published seed content." if order < 3 else "Draft edge state.",
                    "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ" if order < 3 else "",
                    "youtube_duration_seconds": 600 if order < 3 else None,
                    "release_at": timezone.now() - timedelta(days=30) if order == 1 else None,
                    "is_published": order < 3,
                },
            )

        diploma_lectures = {}
        for cohort in cohorts:
            diploma_lectures[cohort.pk] = []
            for semester in semesters:
                for order in range(1, 4):
                    lecture, _ = DiplomaLecture.objects.update_or_create(
                        cohort=cohort,
                        semester=semester,
                        order=order,
                        defaults={
                            "title": f"{cohort.name} / {semester.title} / Lecture {order}",
                            "description": "Seed diploma lecture.",
                            "youtube_url": (
                                "https://www.youtube.com/watch?v=dQw4w9WgXcQ" if order < 3 else ""
                            ),
                            "youtube_duration_seconds": 720 if order < 3 else None,
                            "release_at": timezone.now() - timedelta(days=14) if order == 1 else None,
                            "is_published": order < 3,
                        },
                    )
                    if semester.order == 1 and order < 3:
                        diploma_lectures[cohort.pk].append(lecture)
                        self._ensure_question_bank(lecture, admin)
        return {
            "diploma": diploma,
            "semesters": semesters,
            "cohorts": cohorts,
            "course": course,
            "course_version": course_version,
            "diploma_lectures": diploma_lectures,
        }

    def _ensure_question_bank(self, lecture: DiplomaLecture, admin: User) -> None:
        for number in range(1, 4):
            question, _ = DiplomaLectureQuestion.objects.update_or_create(
                lecture=lecture,
                text=f"[Seed {number}] {lecture.title}: choose the correct answer.",
                defaults={
                    "question_type": QUESTION_TYPE_MCQ,
                    "points": Decimal("5.00"),
                    "active": True,
                    "created_by": admin,
                },
            )
            for order in range(1, 4):
                DiplomaLectureChoice.objects.update_or_create(
                    question=question,
                    order=order,
                    defaults={
                        "text": f"Option {order}",
                        "is_correct": order == 1,
                    },
                )

    def _ensure_population(
        self,
        *,
        cohort: Cohort,
        cohort_number: int,
        count: int,
        course_version: CourseVersion,
        admin: User,
        password: str,
    ) -> list[dict]:
        student_group, _ = Group.objects.get_or_create(name=GROUP_STUDENT)
        rows = []
        active_cutoff = max(2, count - 4)
        for index in range(1, count + 1):
            email = f"seed.c{cohort_number}.{index:04d}@dokkimashora.test"
            phone = f"+2015{cohort_number}{index:08d}"
            national_id = f"8{cohort_number}{index:012d}"
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    "full_name": f"Seed Cohort {cohort_number} Student {index:04d}",
                    "phone": phone,
                    "national_id": national_id,
                    "country_code": "EG",
                    "is_active": True,
                    "email_activated_at": timezone.now(),
                },
            )
            user.full_name = f"Seed Cohort {cohort_number} Student {index:04d}"
            user.phone = phone
            user.national_id = national_id
            user.country_code = "EG"
            user.is_active = True
            user.email_activated_at = user.email_activated_at or timezone.now()
            if password:
                user.set_password(password)
            elif created:
                user.set_unusable_password()
            user.save()
            user.groups.add(student_group)

            profile, _ = StudentProfile.objects.update_or_create(
                user=user,
                defaults={
                    "full_name": user.full_name,
                    "date_of_birth": date(1990 + index % 12, 1 + index % 12, 1 + index % 27),
                    "gender": GENDER_FEMALE if index % 2 == 0 else GENDER_MALE,
                    "address": f"Seed address {cohort_number}-{index}",
                    "phone": phone,
                    "country_code": "EG",
                    "national_id": national_id,
                    "education": EDUCATION_BACHELOR,
                    "specialization": "Counseling",
                    "current_job": "Seed student",
                },
            )

            if index <= active_cutoff:
                desired_status = PROGRAM_APP_STATUS_READY_FOR_ACTIVATION
            elif index == active_cutoff + 1:
                desired_status = PROGRAM_APP_STATUS_INVITED
            elif index == active_cutoff + 2:
                desired_status = PROGRAM_APP_STATUS_SUBMITTED
            elif index == active_cutoff + 3:
                desired_status = PROGRAM_APP_STATUS_REJECTED
            else:
                desired_status = PROGRAM_APP_STATUS_ACCEPTED

            application = ProgramApplication.objects.filter(
                student=profile,
                program_type=PROGRAM_TYPE_DIPLOMA,
                cohort=cohort,
            ).order_by("id").first()
            if application is None:
                application = ProgramApplication(
                    student=profile,
                    program_type=PROGRAM_TYPE_DIPLOMA,
                    cohort=cohort,
                )
            if application.status != PROGRAM_APP_STATUS_ACTIVATED:
                application.status = desired_status
            application.delivery_mode = DELIVERY_MODE_OFFLINE if index % 10 == 0 else DELIVERY_MODE_ONLINE
            application.reviewed_by = admin if desired_status in {
                PROGRAM_APP_STATUS_READY_FOR_ACTIVATION,
                PROGRAM_APP_STATUS_REJECTED,
                PROGRAM_APP_STATUS_ACCEPTED,
            } else None
            application.reviewed_at = timezone.now() if application.reviewed_by else None
            application.rejection_reason = (
                "Seed rejection edge state."
                if desired_status == PROGRAM_APP_STATUS_REJECTED
                else ""
            )
            application.full_clean()
            application.save()

            enrollment = None
            if index <= active_cutoff:
                payment, _ = PaymentRecord.objects.update_or_create(
                    external_reference=f"SEED-C{cohort_number}-{index:04d}",
                    defaults={
                        "student": profile,
                        "application": application,
                        "amount": Decimal("1500.00"),
                        "currency": "EGP",
                        "status": PAYMENT_STATUS_PAID,
                        "paid_at": timezone.now() - timedelta(days=30),
                        "target_type": "diploma",
                        "target_ref": f"cohort:{cohort.pk}",
                        "target_snapshot": {"cohort_id": cohort.pk, "cohort_name": cohort.name},
                        "imported_by": admin,
                    },
                )
                result = activate_application_if_ready(application, actor=admin)
                if not result.activated or result.enrollment is None:
                    raise CommandError(
                        f"Could not activate {email}: {', '.join(result.reasons) or 'unknown reason'}"
                    )
                enrollment = result.enrollment
                payment.diploma_enrollment = enrollment
                payment.save(update_fields=["diploma_enrollment", "updated_at"])
                if index == active_cutoff - 1:
                    enrollment.status = ENROLLMENT_STATUS_RESTRICTED
                    enrollment.save(update_fields=["status", "updated_at"])
                elif index == active_cutoff:
                    enrollment.status = ENROLLMENT_STATUS_EXPIRED
                    enrollment.expires_at = timezone.now() - timedelta(days=1)
                    enrollment.save(update_fields=["status", "expires_at", "updated_at"])
            elif desired_status == PROGRAM_APP_STATUS_ACCEPTED:
                PaymentRecord.objects.update_or_create(
                    external_reference=f"SEED-UNPAID-C{cohort_number}-{index:04d}",
                    defaults={
                        "student": profile,
                        "application": application,
                        "amount": Decimal("1500.00"),
                        "currency": "EGP",
                        "status": PAYMENT_STATUS_UNPAID,
                        "due_at": timezone.now() + timedelta(days=14),
                        "target_type": "diploma",
                        "target_ref": f"cohort:{cohort.pk}",
                    },
                )

            course_enrollment = None
            if index <= min(12, active_cutoff - 2):
                course_enrollment, _ = CourseEnrollment.objects.get_or_create(
                    student=profile,
                    course_version=course_version,
                )
                activate_course_enrollment(course_enrollment)

            rows.append({
                "index": index,
                "user": user,
                "profile": profile,
                "application": application,
                "enrollment": enrollment,
                "course_enrollment": course_enrollment,
            })
        return rows

    def _ensure_assignments(self, graph: dict, populations: dict, staff: dict) -> None:
        admin = staff[GROUP_ADMIN]
        moderator = staff[GROUP_MODERATOR]
        reviewer = staff[GROUP_REVIEWER]
        for cohort in graph["cohorts"]:
            active_rows = [
                row for row in populations[cohort.pk]
                if row["enrollment"] and row["enrollment"].status == ENROLLMENT_STATUS_ACTIVE
            ]
            clusters = []
            for number in range(1, 5):
                cluster, _ = StudentCluster.objects.update_or_create(
                    cohort=cohort,
                    name=f"Seed Cluster {number}",
                    defaults={"created_by": admin},
                )
                clusters.append(cluster)
                ModeratorAssignment.objects.update_or_create(
                    moderator=moderator,
                    cohort=cohort,
                    cluster=cluster,
                    course_version=None,
                    defaults={"active": True, "assigned_by": admin},
                )
            for offset, row in enumerate(active_rows):
                cluster = clusters[offset % len(clusters)]
                membership = StudentClusterMembership.objects.filter(
                    student=row["profile"],
                    cohort=cohort,
                ).first()
                if membership is None:
                    StudentClusterMembership.objects.create(
                        cluster=cluster,
                        student=row["profile"],
                        assigned_by=admin,
                    )
            # Reviewer rows are added after exam sections exist.
            populations[cohort.pk][0]["reviewer"] = reviewer

    def _ensure_assessments(self, graph: dict, admin: User) -> dict:
        now = timezone.now()
        quiz = Assessment.objects.filter(
            assessment_type=ASSESSMENT_TYPE_QUIZ,
            course_version=graph["course_version"],
        ).order_by("id").first()
        if quiz is None:
            quiz = Assessment(
                title="Seed Standalone Course Quiz",
                assessment_type=ASSESSMENT_TYPE_QUIZ,
                course_version=graph["course_version"],
                created_by=admin,
            )
        quiz.open_at = now - timedelta(days=30)
        quiz.close_at = now + timedelta(days=365)
        quiz.duration_minutes = 30
        quiz.total_points = Decimal("10.00")
        quiz.passing_score = Decimal("60.00")
        quiz.max_attempts = 1
        quiz.is_active = True
        quiz.release_mode = RELEASE_MODE_AUTOMATIC
        quiz.show_correct_answers = False
        quiz.full_clean()
        quiz.save()
        for order in (1, 2):
            question, _ = AssessmentQuestion.objects.update_or_create(
                assessment=quiz,
                order=order,
                defaults={
                    "text": f"Seed course quiz question {order}",
                    "question_type": QUESTION_TYPE_MCQ,
                    "points": Decimal("5.00"),
                    "required": True,
                },
            )
            for choice_order in (1, 2, 3):
                AssessmentChoice.objects.update_or_create(
                    question=question,
                    order=choice_order,
                    defaults={
                        "text": f"Choice {choice_order}",
                        "is_correct": choice_order == 1,
                    },
                )

        exams = {}
        homeworks = {}
        for cohort_index, cohort in enumerate(graph["cohorts"]):
            semester = graph["semesters"][0]
            if cohort_index == 0:
                homework, _ = Assessment.objects.update_or_create(
                    title=f"Seed Homework — {cohort.name}",
                    defaults={
                        "description": "Draft aggregate; binary upload intentionally unsupported.",
                        "assessment_type": ASSESSMENT_TYPE_HOMEWORK,
                        "course_version": None,
                        "semester": semester,
                        "cohort": cohort,
                        "open_at": now - timedelta(days=30),
                        "close_at": now + timedelta(days=365),
                        "duration_minutes": None,
                        "total_points": Decimal("20.00"),
                        "passing_score": Decimal("50.00"),
                        "max_attempts": 1,
                        "max_upload_files": 3,
                        "is_active": True,
                        "grade_visibility": GRADE_VISIBILITY_HIDDEN,
                        "release_mode": RELEASE_MODE_MANUAL,
                        "created_by": admin,
                    },
                )
                homework.full_clean()
                homework.save()
                homeworks[cohort.pk] = homework

            exam, _ = Assessment.objects.update_or_create(
                title=f"Seed Semester Exam — {cohort.name}",
                defaults={
                    "description": "Online/offline semester exam seed scenario.",
                    "assessment_type": ASSESSMENT_TYPE_SEMESTER_EXAM,
                    "course_version": None,
                    "semester": semester,
                    "cohort": cohort,
                    "open_at": now - timedelta(days=30),
                    "close_at": now + timedelta(days=365),
                    "duration_minutes": 120,
                    "total_points": Decimal("20.00"),
                    "passing_score": Decimal("60.00"),
                    "max_attempts": 1,
                    "is_active": True,
                    "grade_visibility": GRADE_VISIBILITY_HIDDEN,
                    "release_mode": RELEASE_MODE_MANUAL,
                    "created_by": admin,
                },
            )
            exam.full_clean()
            exam.save()
            for order, lecture in enumerate(graph["diploma_lectures"][cohort.pk], start=1):
                section, _ = AssessmentLectureSection.objects.update_or_create(
                    assessment=exam,
                    lecture=lecture,
                    defaults={
                        "order": order,
                        "mcq_count": 1,
                        "max_grade": Decimal("10.00"),
                    },
                )
                section.full_clean()
                section.save()
                AssessmentSectionManualQuestion.objects.update_or_create(
                    section=section,
                    order=1,
                    defaults={
                        "text": f"Written reflection for {lecture.title}",
                        "question_type": QUESTION_TYPE_WRITTEN,
                        "points": Decimal("5.00"),
                        "required": True,
                        "rubric": "Clear, relevant response.",
                    },
                )
            validate_sections_ready(exam)
            exams[cohort.pk] = exam
        return {"quiz": quiz, "homeworks": homeworks, "exams": exams}

    def _ensure_exam_codes(self, assessments: dict, admin: User) -> None:
        for exam in assessments["exams"].values():
            generate_exam_codes(exam, admin)

    def _ensure_entrance_scenarios(self, graph: dict, populations: dict, admin: User) -> None:
        now = timezone.now()
        for cohort in graph["cohorts"]:
            exam, _ = EntranceExam.objects.update_or_create(
                diploma=graph["diploma"],
                cohort=cohort,
                defaults={
                    "title": f"Seed Entrance Exam — {cohort.name}",
                    "description": "Separate admission assessment seed.",
                    "duration_minutes": 45,
                    "pass_percentage": Decimal("50.00"),
                    "is_active": True,
                    "auto_send_results": False,
                    "open_at": now - timedelta(days=30),
                    "close_at": now + timedelta(days=365),
                    "created_by": admin,
                },
            )
            mcq, _ = EntranceQuestion.objects.update_or_create(
                exam=exam,
                order=1,
                defaults={"text": "Seed entrance MCQ", "question_type": QUESTION_TYPE_MCQ},
            )
            EntranceChoice.objects.update_or_create(
                question=mcq,
                order=1,
                defaults={"text": "Correct", "score": Decimal("10.00")},
            )
            EntranceChoice.objects.update_or_create(
                question=mcq,
                order=2,
                defaults={"text": "Incorrect", "score": Decimal("0.00")},
            )
            written, _ = EntranceQuestion.objects.update_or_create(
                exam=exam,
                order=2,
                defaults={
                    "text": "Why are you applying?",
                    "question_type": QUESTION_TYPE_WRITTEN,
                    "max_score": Decimal("10.00"),
                    "rubric": "Relevant motivation.",
                },
            )
            candidates = [
                row for row in populations[cohort.pk]
                if row["application"].status in {
                    PROGRAM_APP_STATUS_INVITED,
                    PROGRAM_APP_STATUS_SUBMITTED,
                    PROGRAM_APP_STATUS_REJECTED,
                    PROGRAM_APP_STATUS_ACCEPTED,
                }
            ][:3]
            for scenario, row in enumerate(candidates):
                attempt = EntranceAttempt.objects.filter(
                    exam=exam,
                    program_application=row["application"],
                ).first()
                if attempt is None:
                    attempt = assign_attempt(exam, row["application"], row["profile"])
                if scenario >= 1 and attempt.status == ENTRANCE_ASSIGNED:
                    attempt = start_entrance_attempt(attempt)
                if scenario == 2 and attempt.status == ENTRANCE_IN_PROGRESS:
                    correct = mcq.choices.order_by("order").first()
                    attempt = submit_entrance_attempt(attempt, [
                        EntranceAnswerData(question_id=mcq.pk, selected_choice_id=correct.pk),
                        EntranceAnswerData(question_id=written.pk, written_text="Seed motivation response."),
                    ])
                    written_answer = attempt.answers.get(question=written)
                    grade_entrance_answer(
                        written_answer,
                        Decimal("8.00"),
                        "Seed reviewer feedback.",
                        admin,
                    )

    def _ensure_learning_scenarios(
        self,
        graph: dict,
        populations: dict,
        assessments: dict,
        staff: dict,
    ) -> None:
        admin = staff[GROUP_ADMIN]
        reviewer = staff[GROUP_REVIEWER]
        secretary = staff[GROUP_SECRETARY]
        for cohort in graph["cohorts"]:
            rows = [
                row for row in populations[cohort.pk]
                if row["enrollment"] and row["enrollment"].status == ENROLLMENT_STATUS_ACTIVE
            ]
            if not rows:
                continue
            row = rows[0]
            user = row["user"]
            enrollment = row["enrollment"]

            if row["course_enrollment"] and not assessments["quiz"].attempts.filter(student=user).exists():
                attempt = start_assessment(user, assessments["quiz"])
                answers = []
                for question in assessments["quiz"].questions.prefetch_related("choices"):
                    answers.append(AssessmentAnswerData(
                        question_id=question.pk,
                        selected_choice_id=(
                            question.choices.filter(is_correct=True)
                            .values_list("pk", flat=True)
                            .first()
                        ),
                    ))
                submit_assessment(attempt, answers)

            homework = assessments["homeworks"].get(cohort.pk)
            if homework is not None:
                HomeworkSubmission.objects.get_or_create(
                    assessment=homework,
                    student=user,
                    defaults={"status": HOMEWORK_STATUS_UPLOADED},
                )

            exam = assessments["exams"][cohort.pk]
            code = SemesterExamCode.objects.get(assessment=exam, diploma_enrollment=enrollment)
            if code.delivery_mode == DELIVERY_MODE_OFFLINE:
                code.delivery_mode = DELIVERY_MODE_ONLINE
                code.save(update_fields=["delivery_mode", "updated_at"])
            attempt = exam.attempts.filter(student=user).first()
            if attempt is None:
                attempt = start_assessment(user, exam)
                answer_data = {}
                for question in attempt.snapshot_questions.prefetch_related("choices"):
                    if question.question_type == QUESTION_TYPE_MCQ:
                        answer_data[f"q_{question.order}"] = str(
                            question.choices.filter(is_correct_snapshot=True).values_list("pk", flat=True).first()
                        )
                    else:
                        answer_data[f"q_{question.order}"] = "Seed written exam response."
                attempt = submit_diploma_assessment(attempt, answer_data)
                for answer in attempt.snapshot_questions.filter(
                    question_type=QUESTION_TYPE_WRITTEN,
                ).values_list("answers__pk", "points"):
                    grade_diploma_snapshot_answer(
                        answer[0],
                        answer[1],
                        "Seed reviewer feedback.",
                        reviewer,
                    )
                attempt.refresh_from_db()
            if attempt.status == ATTEMPT_STATUS_GRADED and attempt.exam_review_status == "not_reviewed":
                admin_review_semester_exam(attempt.pk, "pass", actor=admin, note="Seed review.")
            attempt.refresh_from_db()
            if attempt.status == ATTEMPT_STATUS_GRADED and not attempt.visible_to_student:
                release_attempt_grades(attempt.pk, actor=admin, reason="Development seed release.")

            self._ensure_reviewer_assignments(exam, reviewer, admin)
            self._ensure_attendance(cohort, graph["semesters"][0], enrollment, secretary, admin)
            config, _ = SemesterEvaluationConfig.objects.update_or_create(
                cohort=cohort,
                semester=graph["semesters"][0],
                defaults={
                    "attendance_weight_percent": Decimal("20.00"),
                    "homework_weight_percent": Decimal("20.00"),
                    "semester_exam_weight_percent": Decimal("60.00"),
                    "attendance_min_percent": Decimal("50.00"),
                    "attendance_required_sessions": 2,
                    "homework_min_percent": Decimal("50.00"),
                    "semester_exam_min_percent": Decimal("50.00"),
                    "overall_min_percent": Decimal("60.00"),
                    "status": EVALUATION_CONFIG_STATUS_ACTIVE,
                    "created_by": admin,
                    "updated_by": admin,
                },
            )
            config.full_clean()
            config.save()
            # Cohort A intentionally keeps an attachment-free Homework draft,
            # so its evaluation remains blocked rather than inventing a stored
            # file. Cohort B has no Homework source and exercises the locked
            # applicable-component reweighting path through canonical services.
            if homework is None:
                evaluation = finalize_semester_evaluation(
                    enrollment,
                    graph["semesters"][0],
                    actor=admin,
                )
                resolve_final_progression(
                    evaluation,
                    actor=admin,
                    final_result=SEMESTER_EVALUATION_PASS,
                )
            self._ensure_certificate_edge(graph["diploma"], enrollment, admin)

    def _ensure_reviewer_assignments(self, exam: Assessment, reviewer: User, admin: User) -> None:
        ReviewerAssignment.objects.update_or_create(
            reviewer=reviewer,
            cohort=exam.cohort,
            semester=exam.semester,
            assessment=exam,
            scope=REVIEWER_SCOPE_EXAM,
            section=None,
            cluster=None,
            defaults={
                "access_mode": REVIEWER_ACCESS_BOTH,
                "active": True,
                "assigned_by": admin,
            },
        )

    def _ensure_attendance(
        self,
        cohort: Cohort,
        semester: Semester,
        enrollment: DiplomaEnrollment,
        secretary: User,
        admin: User,
    ) -> None:
        code = get_or_create_attendance_code_for_enrollment(enrollment, actor=admin)
        raw_token = build_attendance_qr_payload(code).split("attendance:", 1)[1]
        now = timezone.now()
        period, _ = AttendancePeriod.objects.get_or_create(
            cohort=cohort,
            semester=semester,
            defaults={
                "title": "فترة حضور تجريبية",
                "starts_at": now - timedelta(days=5),
                "ends_at": now + timedelta(days=5),
                "status": SESSION_STATUS_OPEN,
                "opened_by": secretary,
                "created_by": admin,
            },
        )
        for number in (1, 2):
            session_date = (now - timedelta(days=number)).date()
            session = period.sessions.filter(
                date=session_date,
            ).first()
            if session is None:
                session = create_attendance_day(
                    period,
                    session_date=session_date,
                    open_at=now - timedelta(days=number, hours=1),
                    close_at=now + timedelta(hours=1),
                    title=f"جلسة حضور تجريبية {number}",
                    actor=secretary,
                )
            if session.status == SESSION_STATUS_OPEN:
                scan_attendance_token(raw_token, session=session, scanned_by=secretary)
                close_attendance_session(session, secretary)
            elif session.status == SESSION_STATUS_CLOSED and not session.records.filter(
                diploma_enrollment=enrollment,
            ).exists():
                raise CommandError("Seed attendance session is closed without its expected record.")

    def _ensure_certificate_edge(
        self,
        diploma: Diploma,
        enrollment: DiplomaEnrollment,
        admin: User,
    ) -> None:
        template = CertificateTemplate.objects.filter(
            diploma=diploma,
            language="ar",
            template_type="completion",
            active=True,
        ).first()
        if template is None:
            template = CertificateTemplate.objects.create(
                diploma=diploma,
                name="Seed Placeholder Certificate",
                language="ar",
                template_type="completion",
                active=True,
                is_placeholder=True,
                created_by=admin,
            )
        GeneratedCertificate.objects.update_or_create(
            certificate_number=f"SEED-FAILED-{enrollment.pk}",
            defaults={
                "student": enrollment.student,
                "diploma_enrollment": None,
                "certificate_template": template,
                "status": CERTIFICATE_STATUS_FAILED,
                "generated_by": admin,
                "metadata_snapshot": {
                    "scope": SEED_SCOPE,
                    "reason": "No fake PDF generated; storage-backed rendering intentionally unsupported.",
                    "enrollment_id": enrollment.pk,
                },
            },
        )

    def _ensure_messages(self, populations: dict, admin: User) -> None:
        recipients = [rows[0]["user"].email for rows in populations.values() if rows]
        for status, recipient in zip(
            (EMAIL_STATUS_QUEUED, EMAIL_STATUS_SENT, EMAIL_STATUS_FAILED),
            (recipients + recipients)[:3],
            strict=False,
        ):
            EmailMessage.objects.update_or_create(
                idempotency_key=f"{SEED_SCOPE}:message:{status}:{recipient}",
                defaults={
                    "to_email": recipient,
                    "subject": f"Seed message: {status}",
                    "body": "Development seed delivery lifecycle record.",
                    "status": status,
                    "error": "Simulated delivery failure." if status == EMAIL_STATUS_FAILED else "",
                    "sent_by": admin,
                    "queued_at": timezone.now(),
                    "sent_at": timezone.now() if status == EMAIL_STATUS_SENT else None,
                    "attempts": 1 if status in {EMAIL_STATUS_SENT, EMAIL_STATUS_FAILED} else 0,
                    "reference": SEED_SCOPE,
                },
            )

    def _ensure_seed_audit(self, admin: User, count: int) -> None:
        audit_target = f"{SEED_SCOPE}:{count}"
        if not AuditEvent.objects.filter(
            action="demo.data_seeded",
            target_type="system",
            target_id=audit_target,
            metadata__scope=SEED_SCOPE,
        ).exists():
            AuditEvent.objects.create(
                actor=admin,
                action="demo.data_seeded",
                target_type="system",
                target_id=audit_target,
                metadata={
                    "scope": SEED_SCOPE,
                    "students_per_cohort": count,
                    "cohort_count": 2,
                },
                ip_address="127.0.0.1",
            )
