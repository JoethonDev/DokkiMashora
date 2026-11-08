"""Seed a broad Arabic manual-QA graph for the local development database.

This command is intentionally separate from the older demo seed commands. It
is destructive only when the operator supplies both ``--purge`` and
``--confirm-purge`` and it refuses production settings.
"""

from __future__ import annotations

import hashlib
import os
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.management import BaseCommand, CommandError, call_command
from django.db import connection, transaction
from django.utils import timezone

from apps.academics.constants import (
    AUDIO_PROCESSING_COMPLETED,
    AUDIO_PROCESSING_PENDING,
    ENROLLMENT_STATUS_ACTIVE,
    ENROLLMENT_STATUS_COMPLETED,
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
    DiplomaLecture,
    Lecture,
    ModeratorAssignment,
    ModeratorStudentAssignment,
    Semester,
    StudentCluster,
    StudentClusterMembership,
)
from apps.accounts.constants import (
    GROUP_ADMIN,
    GROUP_MODERATOR,
    GROUP_REVIEWER,
    GROUP_SECRETARY,
    GROUP_STUDENT,
    GROUP_SUPER_ADMIN,
)
from apps.accounts.models import User
from apps.applications.constants import (
    DECISION_ACTION_ACCEPT,
    DECISION_ACTION_ACTIVATE,
    DECISION_ACTION_ENTRANCE_SUBMITTED,
    DECISION_ACTION_INVITED_TO_ENTRANCE,
    DECISION_ACTION_NOTE,
    DECISION_ACTION_REJECT,
    DECISION_ACTION_REQUEST_DOCUMENTS,
    DECISION_ACTION_REQUEST_INTERVIEW,
    DECISION_ACTION_REQUEST_SURVEY,
    DECISION_ACTION_SUBMIT_SURVEY,
    DELIVERY_MODE_OFFLINE,
    DELIVERY_MODE_ONLINE,
    DOCUMENT_REQUEST_STATUS_EXPIRED,
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_ACTIVATED,
    PROGRAM_APP_STATUS_DOCUMENTS_REQUESTED,
    PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED,
    PROGRAM_APP_STATUS_INTERVIEW_REQUESTED,
    PROGRAM_APP_STATUS_INVITED,
    PROGRAM_APP_STATUS_PAYMENT_PENDING,
    PROGRAM_APP_STATUS_REJECTED,
    PROGRAM_APP_STATUS_SUBMITTED,
    PROGRAM_APP_STATUS_SURVEY_REQUESTED,
    PROGRAM_APP_STATUS_SURVEY_SUBMITTED,
    PROGRAM_TYPE_COURSE,
    PROGRAM_TYPE_DIPLOMA,
)
from apps.applications.documents import request_documents_program_application
from apps.applications.models import (
    ProgramApplication,
    ProgramApplicationDecision,
)
from apps.applications.services import (
    accept_program_application,
    invite_to_entrance_exam,
    is_apply_target_open,
    mark_application_paid,
    request_interview_program_application,
    submit_program_application,
)
from apps.assessments.constants import (
    ASSESSMENT_TYPE_HOMEWORK,
    ASSESSMENT_TYPE_QUIZ,
    ASSESSMENT_TYPE_SEMESTER_EXAM,
    ATTEMPT_STATUS_GRADED,
    ATTEMPT_STATUS_RELEASED,
    ATTEMPT_STATUS_SUBMITTED,
    EXAM_OVERRIDE_ACTIVE,
    GRADE_VISIBILITY_HIDDEN,
    GRADE_VISIBILITY_RELEASED,
    HOMEWORK_STATUS_RELEASED,
    HOMEWORK_STATUS_SUBMITTED,
    HOMEWORK_STATUS_UNDER_REVIEW,
    LEGACY_STATUS_ACTIVE,
    QUESTION_TYPE_MCQ,
    QUESTION_TYPE_WRITTEN,
    RELEASE_MODE_AUTOMATIC,
    RELEASE_MODE_MANUAL,
    REVIEWER_ACCESS_BOTH,
    REVIEWER_SCOPE_EXAM,
    REVIEWER_SCOPE_SEMESTER,
)
from apps.assessments.models import (
    Assessment,
    AssessmentAttempt,
    AssessmentChoice,
    AssessmentLectureSection,
    AssessmentQuestion,
    AssessmentSectionManualQuestion,
    AssessmentSubmission,
    DiplomaLectureChoice,
    DiplomaLectureQuestion,
    HomeworkAttachment,
    HomeworkSubmission,
    OfflineSectionGrade,
    ReviewerAssignment,
    ReviewerExamClaim,
    SemesterExamAccessOverride,
    SemesterExamCode,
)
from apps.assessments.services import (
    AssessmentAnswerData,
    admin_review_semester_exam,
    generate_exam_codes,
    grade_diploma_snapshot_answer,
    start_assessment,
    submit_assessment,
    submit_diploma_assessment,
    validate_sections_ready,
)
from apps.attendance.constants import (
    SESSION_STATUS_CANCELLED,
    SESSION_STATUS_OPEN,
    SESSION_STATUS_SCHEDULED,
)
from apps.attendance.models import AttendancePeriod
from apps.attendance.services import (
    build_attendance_qr_payload,
    close_attendance_session,
    create_attendance_day,
    get_or_create_attendance_code_for_enrollment,
    scan_attendance_token,
)
from apps.certificates.models import CertificateTemplate, GeneratedCertificate
from apps.certificates.services import generate_certificate_pdf
from apps.entrance_exams.constants import (
    ATTEMPT_STATUS_ASSIGNED as ENTRANCE_ATTEMPT_ASSIGNED,
)
from apps.entrance_exams.constants import (
    QUESTION_TYPE_MCQ as ENTRANCE_QUESTION_MCQ,
)
from apps.entrance_exams.constants import (
    QUESTION_TYPE_WRITTEN as ENTRANCE_QUESTION_WRITTEN,
)
from apps.entrance_exams.models import (
    EntranceChoice,
    EntranceExam,
    EntranceQuestion,
)
from apps.entrance_exams.services import (
    AnswerData,
    assign_attempt,
    grade_answer,
    start_attempt,
    submit_attempt,
)
from apps.intake_forms.constants import (
    FIELD_TYPE_BOOLEAN,
    FIELD_TYPE_CHOICE,
    FIELD_TYPE_DATE,
    FIELD_TYPE_MULTI_CHOICE,
    FIELD_TYPE_NUMBER,
    FIELD_TYPE_TEXT,
    FIELD_TYPE_TEXTAREA,
)
from apps.intake_forms.models import (
    IntakeForm,
    IntakeFormField,
)
from apps.intake_forms.services import (
    expire_intake_request,
    request_intake_form_for_application,
    submit_intake_form_response,
)
from apps.payments.services import (
    grant_payment_waiver,
    mark_enrollment_paid,
    mark_enrollment_unpaid,
    revoke_payment_waiver,
)
from apps.progression.constants import (
    CERTIFICATE_STATUS_AVAILABLE,
    CERTIFICATE_STATUS_FAILED,
    CERTIFICATE_STATUS_REVOKED,
    DIPLOMA_COMPLETION_FINAL_EXAM_PENDING,
    DIPLOMA_COMPLETION_GRADUATED,
    DIPLOMA_COMPLETION_LOCKED,
    DIPLOMA_COMPLETION_WITHDRAWN,
    EVALUATION_CONFIG_STATUS_ACTIVE,
    INTERVIEW_DECISION_FAIL,
    INTERVIEW_DECISION_PASS,
    PROMOTION_DECISION_LOCK,
    PROMOTION_DECISION_RETAKE_ASSIGNED,
    REJOIN_STATUS_CANCELLED,
    SEMESTER_EVALUATION_FAIL,
    SEMESTER_EVALUATION_PASS,
    SEMESTER_PROGRESS_IN_PROGRESS,
    SEMESTER_PROGRESS_LOCKED,
    SEMESTER_PROGRESS_NOT_STARTED,
    SEMESTER_PROGRESS_PASSED_CONFIRMED,
    SEMESTER_PROGRESS_RETAKE_REQUIRED,
)
from apps.progression.models import (
    CohortAcademicYear,
    DiplomaProgressionRule,
    FinalProgressionOutcome,
    PromotionDecision,
    SemesterEvaluation,
    SemesterEvaluationConfig,
    SemesterProgressionRule,
    StudentDiplomaCompletion,
    StudentSemesterProgress,
)
from apps.progression.services import (
    activate_rejoin_plan,
    cancel_rejoin_plan,
    complete_final_interview,
    create_rejoin_plan,
    request_final_interview,
    resolve_final_progression,
)
from apps.students.constants import (
    EDUCATION_BACHELOR,
    GENDER_FEMALE,
    GENDER_MALE,
)
from apps.students.models import StudentProfile

SEED_SCOPE = "manual_qa_arabic_2026"
DEFAULT_PASSWORD = "test-1234"


class Command(BaseCommand):
    help = "Purge the local development database and seed a broad Arabic manual-QA graph."

    def add_arguments(self, parser):
        parser.add_argument(
            "--purge",
            action="store_true",
            help="Flush the configured database before seeding.",
        )
        parser.add_argument(
            "--confirm-purge",
            action="store_true",
            help="Explicit confirmation required together with --purge.",
        )
        parser.add_argument(
            "--password",
            default=os.environ.get("DOKKI_MANUAL_QA_PASSWORD", DEFAULT_PASSWORD),
            help="Shared simple password for all seeded accounts.",
        )

    def handle(self, *args, **options):
        if getattr(settings, "PRODUCTION_DEPLOYMENT", False):
            raise CommandError("Refusing to seed while PRODUCTION_DEPLOYMENT is enabled.")
        if not settings.DEBUG:
            raise CommandError("Refusing destructive QA seeding while DEBUG is disabled.")
        if not options["purge"] or not options["confirm_purge"]:
            raise CommandError(
                "This command is destructive. Run it with both --purge and --confirm-purge."
            )
        if not options["password"]:
            raise CommandError("The seeded accounts need a non-empty password.")

        self.password = options["password"]
        self.now = timezone.now()
        self.groups = {}
        self.staff = {}
        self.students = {}
        self.profiles = {}
        self.applications = {}
        self.tokens = {}
        self.graph = {}
        self.assessments = {}
        self.enrollments = {}

        self.stdout.write(self.style.WARNING("Flushing the configured local development database..."))
        self._purge_database()

        with transaction.atomic():
            self._create_roles_and_staff()
            self._create_students()
            self._create_academic_graph()
            self._create_applications()
            self._create_intake_forms()
            self._create_intake_and_documents()
            self._create_enrollments()
            self._create_assessments()
            self._create_entrance_attempts()
            self._create_attendance()
            self._create_learning_attempts()
            self._create_progression_and_rejoins()
            self._create_payments_and_certificates()
            self._create_assignments()

        # Rendering and media storage are slow external effects, so create the
        # available QA artifact only after its source records are committed.
        available_certificate = generate_certificate_pdf(
            self.completed_certificate_enrollment.student,
            self.completed_certificate_enrollment,
            actor=self.staff[GROUP_ADMIN],
        )
        if available_certificate.status != CERTIFICATE_STATUS_AVAILABLE:
            raise CommandError(
                "Could not generate the available QA certificate PDF: "
                f"{(available_certificate.metadata_snapshot or {}).get('error', 'unknown error')}"
            )

        self._print_manifest()

    def _purge_database(self):
        """Flush Django data and handle legacy local tables missing from the app registry."""
        try:
            call_command("flush", interactive=False, verbosity=0)
            return
        except CommandError:
            if connection.vendor != "postgresql":
                raise
            self.stdout.write(self.style.WARNING(
                "Django flush found a legacy table outside the current app registry; "
                "using guarded PostgreSQL CASCADE cleanup."
            ))
            # The failed flush leaves PostgreSQL's transaction aborted. Closing
            # the Django connection forces a clean connection before the
            # explicitly confirmed local cleanup below.
            connection.close()
            with connection.cursor() as cursor:
                tables = [
                    table
                    for table in connection.introspection.table_names(cursor)
                    if table != "django_migrations"
                ]
                if tables:
                    quoted_tables = ", ".join(
                        connection.ops.quote_name(table) for table in tables
                    )
                    cursor.execute(
                        f"TRUNCATE {quoted_tables} RESTART IDENTITY CASCADE"
                    )
            self.stdout.write(self.style.WARNING(
                "Legacy-table cleanup completed; migration history was preserved."
            ))
            return

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    def _create_roles_and_staff(self):
        for role in (
            GROUP_SUPER_ADMIN,
            GROUP_ADMIN,
            GROUP_MODERATOR,
            GROUP_REVIEWER,
            GROUP_SECRETARY,
            GROUP_STUDENT,
        ):
            self.groups[role], _ = Group.objects.get_or_create(name=role)

        definitions = [
            ("superadmin@test.local", "الأب المشرف العام", GROUP_SUPER_ADMIN, True, True),
            ("admin@test.local", "مينا مسؤول القبول", GROUP_ADMIN, True, False),
            ("moderator@test.local", "مارك المشرف الأكاديمي", GROUP_MODERATOR, True, False),
            ("reviewer@test.local", "دانيال المراجع", GROUP_REVIEWER, True, False),
            ("secretary@test.local", "سلمى السكرتيرة", GROUP_SECRETARY, True, False),
        ]
        for email, name, role, is_staff, is_superuser in definitions:
            user = User.objects.create(
                email=email,
                full_name=name,
                full_name_ar=name,
                country_code="EG",
                is_staff=is_staff,
                is_superuser=is_superuser,
                is_active=True,
                email_activated_at=self.now,
            )
            user.set_password(self.password)
            user.save(update_fields=["password", "status", "email_activated_at"])
            user.groups.add(self.groups[role])
            if role == GROUP_SUPER_ADMIN:
                user.groups.add(self.groups[GROUP_ADMIN])
            self.staff[role] = user

    def _create_students(self):
        definitions = [
            ("ahmad", "ahmad.mansour@test.local", "أحمد محمود عبدالعزيز", GENDER_MALE, True),
            ("mariam", "mariam.samir@test.local", "مريم سمير فؤاد", GENDER_FEMALE, True),
            ("youssef", "youssef.ramy@test.local", "يوسف رامي حنا", GENDER_MALE, True),
            ("nour", "nour.ashraf@test.local", "نور أشرف جرجس", GENDER_FEMALE, True),
            ("karim", "karim.wael@test.local", "كريم وائل متري", GENDER_MALE, True),
            ("intake-open", "qa.intake.open@test.local", "سارة فوزي ميخائيل", GENDER_FEMALE, True),
            ("intake-submitted", "qa.intake.submitted@test.local", "ماجد نجيب بطرس", GENDER_MALE, True),
            ("intake-expired", "qa.intake.expired@test.local", "سلمى عادل جاد", GENDER_FEMALE, True),
            ("documents-open", "qa.documents.open@test.local", "بيشوي عاطف صبري", GENDER_MALE, True),
            ("documents-expired", "qa.documents.expired@test.local", "هبة مينا فريد", GENDER_FEMALE, True),
            ("entrance-assigned", "qa.entrance.assigned@test.local", "بطرس نبيل عادل", GENDER_MALE, True),
            ("entrance-progress", "qa.entrance.progress@test.local", "كيرلس سامح فخري", GENDER_MALE, True),
            ("entrance-submitted", "qa.entrance.submitted@test.local", "مينا شوقي فوزي", GENDER_MALE, True),
            ("application-interview", "qa.application.interview@test.local", "مريم فادي شحاتة", GENDER_FEMALE, True),
            ("waiver-active", "qa.waiver.active@test.local", "رؤى فادي جرجس", GENDER_FEMALE, True),
            ("waiver-revoked", "qa.waiver.revoked@test.local", "إياد أشرف كمال", GENDER_MALE, True),
            ("course-v1", "qa.course.v1@test.local", "هاني مجدي يوحنا", GENDER_MALE, True),
            ("course-v2", "qa.course.v2@test.local", "ماريان رأفت صليب", GENDER_FEMALE, True),
            ("course-expired", "qa.course.expired@test.local", "تامر وديع حنا", GENDER_MALE, True),
            ("course-pending", "qa.course.pending@test.local", "داليا حمدي عادل", GENDER_FEMALE, True),
            ("diploma-online", "qa.diploma.online@test.local", "جورج سامح مرقس", GENDER_MALE, True),
            ("diploma-offline", "qa.diploma.offline@test.local", "نيفين صبحي جاد", GENDER_FEMALE, True),
            ("d2-active", "qa.diploma2.active@test.local", "إبرام فؤاد يوسف", GENDER_MALE, True),
            ("status-lab", "qa.progression.states@test.local", "فادي نادر حنا", GENDER_MALE, True),
            ("completed", "qa.diploma.completed@test.local", "فيلبس مينا جرجس", GENDER_MALE, True),
            ("withdrawn", "qa.diploma.withdrawn@test.local", "أماني صادق ميخائيل", GENDER_FEMALE, True),
            ("rejoin-planned", "qa.rejoin.planned@test.local", "عادل نبيل صبري", GENDER_MALE, True),
            ("rejoin-active", "qa.rejoin.active@test.local", "كريستين وائل حنا", GENDER_FEMALE, True),
            ("rejoin-cancelled", "qa.rejoin.cancelled@test.local", "سعيد فوزي مينا", GENDER_MALE, True),
            ("rejoin-missed", "qa.rejoin.missed@test.local", "يوسف جرجس كمال", GENDER_MALE, True),
            ("target-finalized", "qa.target.finalized@test.local", "سارة نبيل عوض", GENDER_FEMALE, True),
            ("interview-pending", "qa.progression.interview.pending@test.local", "مريم فادي شحاته", GENDER_FEMALE, True),
            ("interview-pass", "qa.progression.interview.pass@test.local", "نادر كمال بطرس", GENDER_MALE, True),
            ("interview-fail", "qa.progression.interview.fail@test.local", "بولا عاطف جرجس", GENDER_MALE, True),
            ("inactive", "qa.inactive@test.local", "إسحق رامي فوزي", GENDER_MALE, False),
        ]
        for index, (key, email, name, gender, is_active) in enumerate(definitions, start=1):
            user = User.objects.create(
                email=email,
                full_name=name,
                full_name_ar=name,
                phone=f"0100000{index:04d}",
                country_code="EG",
                national_id=f"298010100{index:05d}",
                is_active=is_active,
                email_activated_at=self.now if is_active else None,
            )
            user.set_password(self.password)
            user.save(update_fields=["password", "status", "email_activated_at"])
            user.groups.add(self.groups[GROUP_STUDENT])
            profile = StudentProfile.objects.create(
                user=user,
                full_name=name,
                date_of_birth=date(1990 + (index % 9), (index % 12) + 1, (index % 24) + 1),
                gender=gender,
                address="شارع الكنيسة، الجيزة",
                phone=user.phone,
                country_code="EG",
                national_id=user.national_id,
                education=EDUCATION_BACHELOR,
                specialization="الخدمة والرعاية الاجتماعية",
                employer="مؤسسة أهلية",
                current_job="خادم / خادمة",
                church="كنيسة العذراء بالجيزة",
                priest_name="الأب يوحنا",
                priest_contact="01009990000",
            )
            self.students[key] = user
            self.profiles[key] = profile

    # ------------------------------------------------------------------
    # Academic graph
    # ------------------------------------------------------------------

    def _create_academic_graph(self):
        admin = self.staff[GROUP_ADMIN]
        d1 = Diploma.objects.create(
            title="دبلوم الإرشاد الأسري والروحي",
            description="برنامج عربي تطبيقي للإرشاد الأسري والروحي وخدمة المجتمع.",
            total_semesters=3,
            is_active=True,
            video_tracking_enabled=True,
            video_completion_percent=80,
            created_by=admin,
        )
        d2 = Diploma.objects.create(
            title="دبلوم الدراسات الكتابية والخدمة",
            description="برنامج ثلاثي الفصول للدراسات الكتابية ومهارات الخدمة.",
            total_semesters=3,
            is_active=True,
            video_tracking_enabled=False,
            created_by=admin,
        )
        course = Course.objects.create(
            title="دورة مهارات التواصل والإرشاد",
            description="دورة مستقلة بنسختين لاختبار الوصول والمحتوى والإصدارات.",
            is_active=True,
            video_tracking_enabled=True,
            video_completion_percent=80,
            created_by=admin,
        )
        version_1 = CourseVersion.objects.create(
            course=course,
            label="الإصدار الأول",
            access_duration_days=180,
            is_active=True,
            created_by=admin,
        )
        version_2 = CourseVersion.objects.create(
            course=course,
            label="الإصدار الثاني",
            access_duration_days=365,
            is_active=True,
            created_by=admin,
        )

        semesters = {}
        for diploma, prefix in ((d1, "دبلوم الإرشاد"), (d2, "دبلوم الدراسات")):
            semesters[diploma.pk] = []
            for order in range(1, 4):
                semesters[diploma.pk].append(
                    Semester.objects.create(
                        diploma=diploma,
                        order=order,
                        title=f"الفصل الدراسي {order} - {prefix}",
                        is_active=True,
                    )
                )

        cohort_a = Cohort.objects.create(
            diploma=d1,
            name="دفعة الإرشاد 2025 - أ",
            start_date=date(2025, 9, 1),
            end_date=date(2027, 2, 28),
            is_active=True,
            created_by=admin,
        )
        cohort_b = Cohort.objects.create(
            diploma=d1,
            name="دفعة الإرشاد 2026 - ب",
            start_date=date(2026, 9, 1),
            end_date=date(2028, 2, 29),
            is_active=True,
            created_by=admin,
        )
        cohort_d2 = Cohort.objects.create(
            diploma=d2,
            name="دفعة الدراسات الكتابية 2025",
            start_date=date(2025, 10, 1),
            end_date=date(2027, 3, 31),
            is_active=True,
            created_by=admin,
        )

        course_lectures = {
            version_1.pk: self._create_course_lectures(version_1, "الأول"),
            version_2.pk: self._create_course_lectures(version_2, "الثاني"),
        }
        diploma_lectures = {}
        for cohort in (cohort_a, cohort_b, cohort_d2):
            for semester in semesters[cohort.diploma_id]:
                diploma_lectures[(cohort.pk, semester.pk)] = self._create_diploma_lectures(
                    cohort, semester,
                )

        self.graph = {
            "d1": d1,
            "d2": d2,
            "course": course,
            "v1": version_1,
            "v2": version_2,
            "semesters": semesters,
            "cohort_a": cohort_a,
            "cohort_b": cohort_b,
            "cohort_d2": cohort_d2,
            "course_lectures": course_lectures,
            "diploma_lectures": diploma_lectures,
        }
        self._create_entrance_exams()
        self._create_progression_rules()

    def _create_course_lectures(self, version, version_label):
        lectures = []
        for order in range(1, 6):
            lecture = Lecture.objects.create(
                course_version=version,
                order=order,
                title=f"النسخة {version_label} - المحاضرة {order}",
                description="محتوى عربي تجريبي لاختبار ترتيب المحاضرات وتعدد الوسائط.",
                youtube_url=(
                    "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
                    if order in (1, 3, 4) else ""
                ),
                youtube_duration_seconds=600 + order * 60 if order in (1, 3, 4) else None,
                audio_url=("https://media.example.test/qa/audio.mp3" if order in (2, 3) else ""),
                audio_storage_key=(
                    f"manual-qa/audio/{version.pk}/{order}.mp3" if order == 2 else ""
                ),
                audio_pending_storage_key=(
                    f"manual-qa/pending/{version.pk}/{order}.mp3" if order == 3 else ""
                ),
                audio_original_filename=(f"محاضرة-{order}.mp3" if order in (2, 3) else ""),
                audio_content_type="audio/mpeg" if order in (2, 3) else "",
                audio_byte_size=245760 if order == 2 else None,
                audio_checksum=("a" * 64 if order == 2 else ""),
                audio_processing_status=(
                    AUDIO_PROCESSING_COMPLETED if order == 2 else AUDIO_PROCESSING_PENDING
                ),
                release_at=(self.now + timedelta(days=2) if order == 4 else None),
                is_published=order != 5,
            )
            lectures.append(lecture)
        return lectures

    def _create_diploma_lectures(self, cohort, semester):
        lectures = []
        for order in range(1, 6):
            lecture = DiplomaLecture.objects.create(
                cohort=cohort,
                semester=semester,
                order=order,
                title=f"{semester.title} - موضوع {order}",
                description="محاضرة عربية داخل مسار الدبلوم.",
                youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ" if order <= 3 else "",
                youtube_duration_seconds=720 if order <= 3 else None,
                is_published=order != 5,
                release_at=self.now + timedelta(days=1) if order == 4 else None,
            )
            lectures.append(lecture)
            if order <= 3:
                question = DiplomaLectureQuestion.objects.create(
                    lecture=lecture,
                    text=f"ما الفكرة الأساسية في {lecture.title}؟",
                    question_type=QUESTION_TYPE_MCQ,
                    points=Decimal("5.00"),
                    active=True,
                    created_by=self.staff[GROUP_ADMIN],
                )
                DiplomaLectureChoice.objects.create(
                    question=question, text="الإجابة الصحيحة", is_correct=True, order=1,
                )
                DiplomaLectureChoice.objects.create(
                    question=question, text="إجابة مشتتة", is_correct=False, order=2,
                )
        return lectures

    def _create_intake_forms(self):
        fields = [
            ("العنوان الحالي", FIELD_TYPE_TEXT, True, [], 1),
            ("نبذة عن خبرتك في الخدمة", FIELD_TYPE_TEXTAREA, True, [], 2),
            ("سنوات الخبرة", FIELD_TYPE_NUMBER, False, [], 3),
            ("تاريخ بدء الخدمة", FIELD_TYPE_DATE, False, [], 4),
            ("نوع الخدمة", FIELD_TYPE_CHOICE, True, ["تعليم", "افتقاد", "إدارة"], 5),
            ("الأيام المناسبة", FIELD_TYPE_MULTI_CHOICE, False, ["السبت", "الأحد", "الجمعة"], 6),
            ("هل توافق على لائحة البرنامج؟", FIELD_TYPE_BOOLEAN, True, [], 7),
        ]

        def create_required_form(title, *, cohort=None, course_version=None):
            form = IntakeForm.objects.create(
                title=title,
                description="استمارة عربية مطلوبة قبل تفعيل الالتحاق.",
                cohort=cohort,
                course_version=course_version,
                is_required=True,
                is_active=True,
                open_at=self.now - timedelta(days=30),
                close_at=self.now + timedelta(days=90),
                created_by=self.staff[GROUP_ADMIN],
            )
            for label, field_type, required, options, order in fields:
                IntakeFormField.objects.create(
                    form=form,
                    label=label,
                    field_type=field_type,
                    required=required,
                    options=options,
                    order=order,
                    is_active=True,
                )
            return form

        self.graph["intake_form"] = create_required_form(
            f"استمارة البيانات الأولية - {self.graph['cohort_a'].name}",
            cohort=self.graph["cohort_a"],
        )
        create_required_form(
            f"استمارة البيانات الأولية - {self.graph['cohort_b'].name}",
            cohort=self.graph["cohort_b"],
        )
        self.graph["course_intake_form"] = create_required_form(
            "استمارة البيانات الأولية - دورة مهارات التواصل والإرشاد",
            course_version=self.graph["v2"],
        )

    def _create_entrance_exams(self):
        exams = {}
        for cohort in (self.graph["cohort_a"], self.graph["cohort_b"]):
            exam = EntranceExam.objects.create(
                title=f"اختبار القبول - {cohort.name}",
                description="اختبار قبول منفصل عن تقييمات الفصول الدراسية.",
                diploma=cohort.diploma,
                cohort=cohort,
                duration_minutes=45,
                pass_percentage=Decimal("60.00"),
                is_active=True,
                auto_send_results=False,
                open_at=self.now - timedelta(days=30),
                close_at=self.now + timedelta(days=90),
                created_by=self.staff[GROUP_ADMIN],
            )
            q1 = EntranceQuestion.objects.create(
                exam=exam,
                text="ما الهدف الأساسي من الاستماع الفعال؟",
                question_type=ENTRANCE_QUESTION_MCQ,
                order=1,
            )
            EntranceChoice.objects.create(question=q1, text="فهم المتحدث قبل الحكم", score=Decimal("10"), order=1)
            EntranceChoice.objects.create(question=q1, text="إنهاء الحديث بسرعة", score=Decimal("0"), order=2)
            EntranceQuestion.objects.create(
                exam=exam,
                text="اكتب في سطرين سبب رغبتك في الالتحاق.",
                question_type=ENTRANCE_QUESTION_WRITTEN,
                order=2,
                max_score=Decimal("10"),
                rubric="وضوح الدافع والارتباط بالخدمة.",
            )
            exam.recompute_total_score()
            exams[cohort.pk] = exam
            if cohort == self.graph["cohort_a"]:
                self.graph["entrance_exam"] = exam
        self.graph["entrance_exams"] = exams

    def _create_progression_rules(self):
        for diploma in (self.graph["d1"], self.graph["d2"]):
            # The Diploma post-save signal creates the first active rule.
            # Reconfigure that row instead of inserting a second active rule.
            rule = DiplomaProgressionRule.objects.get(diploma=diploma, is_active=True)
            rule.semester_pass_percent = Decimal("60.00")
            rule.final_exam_pass_percent = Decimal("60.00")
            rule.require_all_semesters_passed = True
            rule.require_final_exam = False
            rule.auto_promote_enabled = False
            rule.allow_admin_override = True
            rule.allow_retake = True
            rule.max_retake_count = 1
            rule.created_by = self.staff[GROUP_ADMIN]
            rule.save()
            for semester in self.graph["semesters"][diploma.pk]:
                SemesterProgressionRule.objects.create(
                    diploma_rule=rule,
                    semester=semester,
                    pass_percent=Decimal("60.00"),
                    require_all_required_assessments=True,
                    allow_missing_optional_assessments=False,
                    is_active=True,
                )
        for cohort in (self.graph["cohort_a"], self.graph["cohort_b"], self.graph["cohort_d2"]):
            CohortAcademicYear.objects.create(
                cohort=cohort,
                year_number=1,
                start_date=cohort.start_date,
                end_date=cohort.start_date.replace(year=cohort.start_date.year + 1) - timedelta(days=1),
                semesters_per_year=2,
            )
            CohortAcademicYear.objects.create(
                cohort=cohort,
                year_number=2,
                start_date=cohort.start_date.replace(year=cohort.start_date.year + 1),
                end_date=cohort.start_date.replace(year=cohort.start_date.year + 2) - timedelta(days=1),
                semesters_per_year=1,
            )
            for semester in self.graph["semesters"][cohort.diploma_id]:
                homework_weight = Decimal("20.00") if cohort.diploma_id == self.graph["d1"].pk else Decimal("0.00")
                SemesterEvaluationConfig.objects.create(
                    cohort=cohort,
                    semester=semester,
                    attendance_weight_percent=Decimal("20.00") if homework_weight else Decimal("40.00"),
                    homework_weight_percent=homework_weight,
                    semester_exam_weight_percent=Decimal("60.00"),
                    attendance_min_percent=Decimal("80.00"),
                    attendance_required_sessions=2,
                    homework_min_percent=Decimal("50.00"),
                    semester_exam_min_percent=Decimal("50.00"),
                    overall_min_percent=Decimal("60.00"),
                    status=EVALUATION_CONFIG_STATUS_ACTIVE,
                    created_by=self.staff[GROUP_ADMIN],
                    updated_by=self.staff[GROUP_ADMIN],
                )

    # ------------------------------------------------------------------
    # Applications, intake, and entrance
    # ------------------------------------------------------------------

    def _new_application(self, key, student_key, program_type, *, cohort=None, course=None, delivery=DELIVERY_MODE_ONLINE, status=PROGRAM_APP_STATUS_SUBMITTED):
        profile = self.profiles[student_key]
        if (
            program_type == PROGRAM_TYPE_DIPLOMA
            and cohort is not None
            and not is_apply_target_open(cohort=cohort)
        ):
            # The public submission workflow intentionally requires the
            # cohort's entrance exam window to contain now. This approved QA
            # diploma has no such exam, so create its submitted dashboard
            # fixture at the model boundary without inventing an exam that
            # should not exist.
            application = ProgramApplication.objects.create(
                student=profile,
                program_type=program_type,
                cohort=cohort,
                delivery_mode=delivery,
                status=PROGRAM_APP_STATUS_SUBMITTED,
            )
        else:
            application = submit_program_application(
                profile,
                program_type,
                cohort=cohort,
                course=course,
                delivery_mode=delivery,
            )
        if status != PROGRAM_APP_STATUS_SUBMITTED:
            self._set_application_status(application, status)
        self.applications[key] = application
        return application

    def _set_application_status(self, application, status, *, old_status=None, reason="بيانات QA"):
        old_status = old_status or application.status
        action = {
            PROGRAM_APP_STATUS_ACCEPTED: DECISION_ACTION_ACCEPT,
            PROGRAM_APP_STATUS_ACTIVATED: DECISION_ACTION_ACTIVATE,
            PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED: DECISION_ACTION_ENTRANCE_SUBMITTED,
            PROGRAM_APP_STATUS_INVITED: DECISION_ACTION_INVITED_TO_ENTRANCE,
            PROGRAM_APP_STATUS_INTERVIEW_REQUESTED: DECISION_ACTION_REQUEST_INTERVIEW,
            PROGRAM_APP_STATUS_REJECTED: DECISION_ACTION_REJECT,
            PROGRAM_APP_STATUS_DOCUMENTS_REQUESTED: DECISION_ACTION_REQUEST_DOCUMENTS,
            PROGRAM_APP_STATUS_SURVEY_REQUESTED: DECISION_ACTION_REQUEST_SURVEY,
            PROGRAM_APP_STATUS_SURVEY_SUBMITTED: DECISION_ACTION_SUBMIT_SURVEY,
            PROGRAM_APP_STATUS_PAYMENT_PENDING: "payment_pending",
        }.get(status, DECISION_ACTION_NOTE)
        application.status = status
        if status in {
            PROGRAM_APP_STATUS_ACCEPTED,
            PROGRAM_APP_STATUS_ACTIVATED,
            PROGRAM_APP_STATUS_REJECTED,
        }:
            application.reviewed_by = self.staff[GROUP_ADMIN]
            application.reviewed_at = self.now
        application.admin_notes = reason
        if status == PROGRAM_APP_STATUS_REJECTED:
            application.rejection_reason = reason
        application.save(
            update_fields=[
                "status", "reviewed_by", "reviewed_at", "admin_notes",
                "rejection_reason", "updated_at",
            ]
        )
        ProgramApplicationDecision.objects.create(
            program_application=application,
            action=action,
            old_status=old_status,
            new_status=status,
            actor=self.staff[GROUP_ADMIN],
            reason=reason,
            metadata={"seed_scope": SEED_SCOPE},
        )

    def _create_applications(self):
        cohort_a = self.graph["cohort_a"]
        cohort_d2 = self.graph["cohort_d2"]
        course = self.graph["course"]

        # The five core users requested for the acceptance/review test.
        self._new_application("core_diploma_1", "ahmad", PROGRAM_TYPE_DIPLOMA, cohort=cohort_a)
        self._new_application("core_diploma_2", "mariam", PROGRAM_TYPE_DIPLOMA, cohort=cohort_d2, delivery=DELIVERY_MODE_OFFLINE)
        self._new_application("core_course", "youssef", PROGRAM_TYPE_COURSE, course=course)

        # Admission lifecycle states.
        for key, student_key in (
            ("intake_open", "intake-open"),
            ("intake_submitted", "intake-submitted"),
            ("intake_expired", "intake-expired"),
        ):
            application = self._new_application(
                key,
                student_key,
                PROGRAM_TYPE_COURSE,
                course=course,
            )
            self._set_application_status(application, PROGRAM_APP_STATUS_ACCEPTED)
        self._new_application(
            "documents_open",
            "documents-open",
            PROGRAM_TYPE_DIPLOMA,
            cohort=cohort_a,
            status=PROGRAM_APP_STATUS_ACCEPTED,
        )
        self._new_application(
            "documents_expired",
            "documents-expired",
            PROGRAM_TYPE_DIPLOMA,
            cohort=cohort_a,
            status=PROGRAM_APP_STATUS_ACCEPTED,
        )
        self._new_application("entrance_assigned", "entrance-assigned", PROGRAM_TYPE_DIPLOMA, cohort=cohort_a)
        self._new_application("entrance_progress", "entrance-progress", PROGRAM_TYPE_DIPLOMA, cohort=cohort_a)
        self._new_application("entrance_submitted", "entrance-submitted", PROGRAM_TYPE_DIPLOMA, cohort=cohort_a)
        self._new_application("application_interview", "application-interview", PROGRAM_TYPE_DIPLOMA, cohort=cohort_a)
        self._new_application(
            "waiver_active",
            "waiver-active",
            PROGRAM_TYPE_COURSE,
            course=course,
            status=PROGRAM_APP_STATUS_ACCEPTED,
        )
        self._new_application(
            "waiver_revoked",
            "waiver-revoked",
            PROGRAM_TYPE_COURSE,
            course=course,
            status=PROGRAM_APP_STATUS_ACCEPTED,
        )

        # Course activation is deliberately run through the canonical service.
        course_service = self._new_application("course_service", "course-v2", PROGRAM_TYPE_COURSE, course=course)
        accept_program_application(course_service, self.staff[GROUP_ADMIN], send_email=False)
        course_service.refresh_from_db()
        if course_service.status not in {PROGRAM_APP_STATUS_PAYMENT_PENDING, PROGRAM_APP_STATUS_ACCEPTED}:
            raise CommandError(f"Unexpected course seed status: {course_service.status}")
        mark_application_paid(
            course_service,
            self.staff[GROUP_ADMIN],
            amount=Decimal("850.00"),
            external_reference="QA-COURSE-PAID-001",
            notes="دفع تجريبي عبر خدمة الدفع.",
        )

        # Activated diploma applications are paired with direct enrollments
        # below because actual uploaded document binaries are intentionally not
        # fabricated by this seed.
        for key, student_key, delivery in (
            ("diploma_online", "diploma-online", DELIVERY_MODE_ONLINE),
            ("diploma_offline", "diploma-offline", DELIVERY_MODE_OFFLINE),
            ("d2_active", "d2-active", DELIVERY_MODE_ONLINE),
            ("status_lab", "status-lab", DELIVERY_MODE_ONLINE),
            ("completed", "completed", DELIVERY_MODE_ONLINE),
            ("withdrawn", "withdrawn", DELIVERY_MODE_ONLINE),
            ("rejoin_planned", "rejoin-planned", DELIVERY_MODE_ONLINE),
            ("rejoin_active", "rejoin-active", DELIVERY_MODE_ONLINE),
            ("rejoin_cancelled", "rejoin-cancelled", DELIVERY_MODE_ONLINE),
            ("rejoin_missed", "rejoin-missed", DELIVERY_MODE_ONLINE),
            ("target_finalized", "target-finalized", DELIVERY_MODE_ONLINE),
            ("interview_pending", "interview-pending", DELIVERY_MODE_ONLINE),
            ("interview_pass", "interview-pass", DELIVERY_MODE_ONLINE),
            ("interview_fail", "interview-fail", DELIVERY_MODE_ONLINE),
        ):
            app = self._new_application(
                key,
                student_key,
                PROGRAM_TYPE_DIPLOMA,
                cohort=cohort_d2 if student_key == "d2-active" else cohort_a,
                delivery=delivery,
            )
            self._set_application_status(app, PROGRAM_APP_STATUS_ACTIVATED, reason="حالة قبول مفعلة لاختبار المسار الأكاديمي.")

        self._new_application("course_v1", "course-v1", PROGRAM_TYPE_COURSE, course=course, status=PROGRAM_APP_STATUS_ACTIVATED)
        self._new_application("course_expired", "course-expired", PROGRAM_TYPE_COURSE, course=course, status=PROGRAM_APP_STATUS_ACTIVATED)
        self._new_application("course_pending", "course-pending", PROGRAM_TYPE_COURSE, course=course, status=PROGRAM_APP_STATUS_PAYMENT_PENDING)
        self._new_application("rejected", "inactive", PROGRAM_TYPE_COURSE, course=course, status=PROGRAM_APP_STATUS_REJECTED)

    def _replace_token(self, row, raw_token):
        row.token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        row.save(update_fields=["token_hash"])
        return raw_token

    def _create_intake_and_documents(self):
        form = self.graph["course_intake_form"]
        admin = self.staff[GROUP_ADMIN]

        open_request = request_intake_form_for_application(
            self.applications["intake_open"],
            admin,
            expires_at=self.now + timedelta(days=14),
            form=form,
            send_email=False,
        )
        self.tokens["intake_open"] = self._replace_token(open_request, "qa-intake-open-token")

        submitted_request = request_intake_form_for_application(
            self.applications["intake_submitted"],
            admin,
            expires_at=self.now + timedelta(days=14),
            form=form,
            send_email=False,
        )
        answers = {}
        for field in form.fields.all():
            if field.field_type == FIELD_TYPE_NUMBER:
                answers[str(field.pk)] = 4
            elif field.field_type == FIELD_TYPE_DATE:
                answers[str(field.pk)] = "2020-01-01"
            elif field.field_type == FIELD_TYPE_CHOICE:
                answers[str(field.pk)] = field.options[0]
            elif field.field_type == FIELD_TYPE_MULTI_CHOICE:
                answers[str(field.pk)] = field.options[:2]
            elif field.field_type == FIELD_TYPE_BOOLEAN:
                answers[str(field.pk)] = True
            elif field.field_type == FIELD_TYPE_TEXTAREA:
                answers[str(field.pk)] = "أرغب في تطوير خدمتي ومساندة الأسر باحترام وخصوصية."
            else:
                answers[str(field.pk)] = "الجيزة"
        submit_intake_form_response(
            submitted_request,
            self.students["intake-submitted"],
            application=self.applications["intake_submitted"],
            answers=answers,
        )

        # Keep waiver lifecycle examples on course applications so they can
        # pass the required survey gate without fabricating uploaded diploma
        # document binaries.
        for key in ("waiver_active", "waiver_revoked"):
            application = self.applications[key]
            waiver_request = request_intake_form_for_application(
                application,
                admin,
                expires_at=self.now + timedelta(days=14),
                form=form,
                send_email=False,
            )
            submit_intake_form_response(
                waiver_request,
                application.student.user,
                application=application,
                answers=answers,
            )

        expired_request = request_intake_form_for_application(
            self.applications["intake_expired"],
            admin,
            expires_at=self.now - timedelta(days=1),
            form=form,
            send_email=False,
        )
        expire_intake_request(expired_request)

        for key, expires_at in (("documents_open", self.now + timedelta(days=14)), ("documents_expired", self.now + timedelta(days=14))):
            request = request_documents_program_application(
                self.applications[key],
                admin,
                expires_at=expires_at,
                notes="يرجى رفع صورة البطاقة والمستندات المطلوبة.",
                send_email=False,
            )
            raw_token = f"qa-document-{key}-token"
            self.tokens[key] = self._replace_token(request, raw_token)
            if key == "documents_expired":
                request.status = DOCUMENT_REQUEST_STATUS_EXPIRED
                request.expires_at = self.now - timedelta(days=1)
                request.save(update_fields=["status", "expires_at", "updated_at"])

        # Keep one explicit rejected state with a historical decision.
        rejected = self.applications["rejected"]
        rejected.rejection_reason = "المستندات غير مكتملة في الطلب التجريبي."
        rejected.save(update_fields=["rejection_reason", "updated_at"])

    # ------------------------------------------------------------------
    # Enrollments and content assessments
    # ------------------------------------------------------------------

    def _diploma_enrollment(self, key, student_key, cohort, *, status=ENROLLMENT_STATUS_ACTIVE, entry_semester=None, expires_at=None):
        enrollment = DiplomaEnrollment.objects.create(
            student=self.profiles[student_key],
            cohort=cohort,
            status=status,
            activated_at=self.now - timedelta(days=120) if status != ENROLLMENT_STATUS_PENDING else None,
            expires_at=expires_at,
            entry_semester=entry_semester or self.graph["semesters"][cohort.diploma_id][0],
        )
        self.enrollments[key] = enrollment
        return enrollment

    def _course_enrollment(self, key, student_key, version, *, status=ENROLLMENT_STATUS_ACTIVE, expires_at=None):
        enrollment = CourseEnrollment.objects.create(
            student=self.profiles[student_key],
            course_version=version,
            status=status,
            activated_at=self.now - timedelta(days=60) if status != ENROLLMENT_STATUS_PENDING else None,
            expires_at=expires_at,
        )
        self.enrollments[key] = enrollment
        return enrollment

    def _create_enrollments(self):
        cohort_a = self.graph["cohort_a"]
        cohort_b = self.graph["cohort_b"]
        cohort_d2 = self.graph["cohort_d2"]
        self._diploma_enrollment("diploma_online", "diploma-online", cohort_a)
        self._diploma_enrollment("diploma_offline", "diploma-offline", cohort_a)
        self._diploma_enrollment("d2_active", "d2-active", cohort_d2)
        self._diploma_enrollment("status_lab", "status-lab", cohort_a)
        self._diploma_enrollment("completed", "completed", cohort_a, status=ENROLLMENT_STATUS_COMPLETED)
        self._diploma_enrollment("withdrawn", "withdrawn", cohort_a, status=ENROLLMENT_STATUS_RESTRICTED)
        self._diploma_enrollment("rejoin_planned", "rejoin-planned", cohort_a)
        self._diploma_enrollment("rejoin_active_source", "rejoin-active", cohort_a)
        self._diploma_enrollment("rejoin_cancelled_source", "rejoin-cancelled", cohort_a)
        self._diploma_enrollment("rejoin_missed", "rejoin-missed", cohort_a)
        self._diploma_enrollment("target_finalized", "target-finalized", cohort_b)
        self._diploma_enrollment("interview_pending", "interview-pending", cohort_a)
        self._diploma_enrollment("interview_pass", "interview-pass", cohort_a)
        self._diploma_enrollment("interview_fail", "interview-fail", cohort_a)
        self._diploma_enrollment("expired_diploma", "karim", cohort_a, status=ENROLLMENT_STATUS_EXPIRED, expires_at=self.now - timedelta(days=10))
        self._diploma_enrollment("pending_diploma", "nour", cohort_d2, status=ENROLLMENT_STATUS_PENDING)

        self._course_enrollment("course_v1", "course-v1", self.graph["v1"])
        # The canonical paid course application creates the V2 enrollment.
        self.enrollments["course_v2"] = CourseEnrollment.objects.get(
            student=self.profiles["course-v2"], course_version=self.graph["v2"],
        )
        self._course_enrollment("course_expired", "course-expired", self.graph["v1"], status=ENROLLMENT_STATUS_EXPIRED, expires_at=self.now - timedelta(days=5))
        self._course_enrollment("course_pending", "course-pending", self.graph["v1"], status=ENROLLMENT_STATUS_PENDING)
        self._course_enrollment("course_completed", "karim", self.graph["v2"], status=ENROLLMENT_STATUS_COMPLETED)

        for enrollment in self.enrollments.values():
            if isinstance(enrollment, DiplomaEnrollment):
                for semester in self.graph["semesters"][enrollment.cohort.diploma_id]:
                    StudentSemesterProgress.objects.get_or_create(
                        student=enrollment.student,
                        diploma_enrollment=enrollment,
                        cohort=enrollment.cohort,
                        semester=semester,
                        defaults={"status": SEMESTER_PROGRESS_NOT_STARTED},
                    )
        # The second diploma intentionally has no intake form and no EntranceExam.
        # Its progression config below uses Semester Exam + Attendance only.
        self._create_clusters()

    def _create_clusters(self):
        admin = self.staff[GROUP_ADMIN]
        moderator = self.staff[GROUP_MODERATOR]
        for cohort in (self.graph["cohort_a"], self.graph["cohort_b"], self.graph["cohort_d2"]):
            cluster = StudentCluster.objects.create(
                name=f"مجموعة الاختبار - {cohort.name}",
                cohort=cohort,
                created_by=admin,
            )
            members = [
                enrollment for enrollment in self.enrollments.values()
                if isinstance(enrollment, DiplomaEnrollment)
                and enrollment.cohort_id == cohort.pk
                and enrollment.status == ENROLLMENT_STATUS_ACTIVE
            ][:3]
            for enrollment in members:
                StudentClusterMembership.objects.create(
                    cluster=cluster,
                    student=enrollment.student,
                    cohort=cohort,
                    assigned_by=admin,
                )
                ModeratorStudentAssignment.objects.create(
                    moderator=moderator,
                    student=enrollment.student,
                    cohort=cohort,
                    assigned_by=admin,
                )
            ModeratorAssignment.objects.create(
                moderator=moderator,
                cohort=cohort,
                cluster=cluster,
                active=True,
                assigned_by=admin,
            )

    def _assessment(self, title, assessment_type, *, course_version=None, semester=None, cohort=None, open_at=None, close_at=None, duration_minutes=30, total_points=Decimal("10.00"), passing_score=Decimal("60.00"), release_mode=RELEASE_MODE_MANUAL, grade_visibility=GRADE_VISIBILITY_HIDDEN, max_upload_files=1, is_required_for_semester_evaluation=True):
        assessment = Assessment.objects.create(
            title=title,
            description="بيانات تقييم عربية لاختبار الحالات الأساسية والنادرة.",
            assessment_type=assessment_type,
            is_required_for_semester_evaluation=is_required_for_semester_evaluation,
            course_version=course_version,
            semester=semester,
            cohort=cohort,
            open_at=open_at or self.now - timedelta(days=7),
            close_at=close_at or self.now + timedelta(days=30),
            duration_minutes=duration_minutes,
            total_points=total_points,
            passing_score=passing_score,
            max_attempts=1,
            max_upload_files=max_upload_files,
            is_active=True,
            show_correct_answers=False,
            show_written_feedback=True,
            grade_visibility=grade_visibility,
            release_mode=release_mode,
            allow_late_submission=False,
            created_by=self.staff[GROUP_ADMIN],
            legacy_status=LEGACY_STATUS_ACTIVE,
        )
        assessment.full_clean()
        return assessment

    def _course_quiz_questions(self, assessment, *, include_written=False):
        for order in range(1, 3):
            question = AssessmentQuestion.objects.create(
                assessment=assessment,
                text=f"اختر الإجابة الأدق للسؤال {order}.",
                question_type=QUESTION_TYPE_MCQ,
                points=Decimal("5.00"),
                order=order,
                required=True,
            )
            AssessmentChoice.objects.create(question=question, text="الإجابة الأولى", is_correct=True, order=1)
            AssessmentChoice.objects.create(question=question, text="الإجابة الثانية", is_correct=False, order=2)
        if include_written:
            AssessmentQuestion.objects.create(
                assessment=assessment,
                text="اكتب مثالاً عملياً من خدمتك.",
                question_type=QUESTION_TYPE_WRITTEN,
                points=Decimal("5.00"),
                order=3,
                required=True,
                rubric="الوضوح وربط الإجابة بالمادة.",
            )

    def _diploma_exam(self, title, cohort, semester, *, open_at=None, close_at=None):
        exam = self._assessment(
            title,
            ASSESSMENT_TYPE_SEMESTER_EXAM,
            semester=semester,
            cohort=cohort,
            open_at=open_at,
            close_at=close_at,
            duration_minutes=90,
            total_points=Decimal("30.00"),
            passing_score=Decimal("50.00"),
            release_mode=RELEASE_MODE_MANUAL,
        )
        for order, lecture in enumerate(self.graph["diploma_lectures"][(cohort.pk, semester.pk)][:3], start=1):
            section = AssessmentLectureSection.objects.create(
                assessment=exam,
                lecture=lecture,
                order=order,
                mcq_count=1,
                max_grade=Decimal("10.00"),
            )
            AssessmentSectionManualQuestion.objects.create(
                section=section,
                text=f"سؤال تحريري عن {lecture.title}.",
                question_type=QUESTION_TYPE_WRITTEN,
                points=Decimal("5.00"),
                order=1,
                required=True,
                rubric="إجابة مرتبطة بالمحاضرة ومبنية على فهم.",
            )
        validate_sections_ready(exam)
        return exam

    def _create_assessments(self):
        v1_quiz = self._assessment(
            "اختبار الدورة - الإصدار الأول",
            ASSESSMENT_TYPE_QUIZ,
            course_version=self.graph["v1"],
            release_mode=RELEASE_MODE_AUTOMATIC,
            grade_visibility=GRADE_VISIBILITY_RELEASED,
        )
        self._course_quiz_questions(v1_quiz)
        v2_quiz = self._assessment(
            "اختبار الدورة - الإصدار الثاني المختلط",
            ASSESSMENT_TYPE_QUIZ,
            course_version=self.graph["v2"],
            release_mode=RELEASE_MODE_MANUAL,
        )
        self._course_quiz_questions(v2_quiz, include_written=True)
        future_quiz = self._assessment(
            "اختبار الدورة - لم يفتح بعد",
            ASSESSMENT_TYPE_QUIZ,
            course_version=self.graph["v2"],
            open_at=self.now + timedelta(days=2),
            close_at=self.now + timedelta(days=10),
        )
        self._course_quiz_questions(future_quiz)

        d1_semesters = self.graph["semesters"][self.graph["d1"].pk]
        d2_semesters = self.graph["semesters"][self.graph["d2"].pk]
        homework = self._assessment(
            "واجب الفصل الأول - الإرشاد الأسري",
            ASSESSMENT_TYPE_HOMEWORK,
            semester=d1_semesters[0],
            cohort=self.graph["cohort_a"],
            duration_minutes=None,
            total_points=Decimal("20.00"),
            passing_score=Decimal("50.00"),
            max_upload_files=3,
        )
        optional_homework = self._assessment(
            "واجب اختياري للفصل الأول - الإرشاد الأسري",
            ASSESSMENT_TYPE_HOMEWORK,
            semester=d1_semesters[0],
            cohort=self.graph["cohort_a"],
            duration_minutes=None,
            total_points=Decimal("60.00"),
            passing_score=Decimal("50.00"),
            max_upload_files=3,
            is_required_for_semester_evaluation=False,
        )
        exam_a = self._diploma_exam(
            "امتحان الفصل الأول - الإرشاد الأسري",
            self.graph["cohort_a"],
            d1_semesters[0],
        )
        exam_b = self._diploma_exam(
            "امتحان الفصل الأول - دفعة الإرشاد الجديدة",
            self.graph["cohort_b"],
            d1_semesters[0],
            open_at=self.now + timedelta(days=1),
            close_at=self.now + timedelta(days=20),
        )
        exam_a_sem2 = self._diploma_exam(
            "امتحان الفصل الثاني - مغلق للاختبار",
            self.graph["cohort_a"],
            d1_semesters[1],
            open_at=self.now - timedelta(days=30),
            close_at=self.now - timedelta(days=1),
        )
        d2_exam = self._diploma_exam(
            "امتحان الفصل الأول - الدراسات الكتابية",
            self.graph["cohort_d2"],
            d2_semesters[0],
        )
        self.assessments.update({
            "course_v1_quiz": v1_quiz,
            "course_v2_quiz": v2_quiz,
            "future_quiz": future_quiz,
            "homework": homework,
            "homework_optional": optional_homework,
            "exam_a": exam_a,
            "exam_b": exam_b,
            "exam_a_sem2": exam_a_sem2,
            "d2_exam": d2_exam,
        })
        for exam in (exam_a, exam_b, exam_a_sem2, d2_exam):
            generate_exam_codes(exam, self.staff[GROUP_ADMIN])

        # One active exceptional-access row for a course quiz and one for a
        # diploma exam. The workflow can be consumed later from the UI.
        SemesterExamAccessOverride.objects.create(
            assessment=v1_quiz,
            course_enrollment=self.enrollments["course_v1"],
            open_at=self.now - timedelta(hours=1),
            close_at=self.now + timedelta(days=1),
            max_attempts=1,
            status=EXAM_OVERRIDE_ACTIVE,
            reason="اختبار وصول استثنائي لحالة فقدان الموعد.",
            created_by=self.staff[GROUP_ADMIN],
        )
        SemesterExamAccessOverride.objects.create(
            assessment=exam_a,
            diploma_enrollment=self.enrollments["diploma_online"],
            open_at=self.now - timedelta(hours=1),
            close_at=self.now + timedelta(days=1),
            max_attempts=1,
            status=EXAM_OVERRIDE_ACTIVE,
            reason="إتاحة استثنائية موثقة لامتحان الفصل.",
            created_by=self.staff[GROUP_ADMIN],
        )

    # ------------------------------------------------------------------
    # Entrance and attendance
    # ------------------------------------------------------------------

    def _create_entrance_attempts(self):
        exam = self.graph["entrance_exam"]
        assigned_app = self.applications["entrance_assigned"]
        progress_app = self.applications["entrance_progress"]
        submitted_app = self.applications["entrance_submitted"]
        admin = self.staff[GROUP_ADMIN]
        for application in (assigned_app, progress_app, submitted_app):
            invite_to_entrance_exam(application, admin)
            application.refresh_from_db()

        assigned_attempt = assign_attempt(exam, assigned_app, self.profiles["entrance-assigned"])
        if assigned_attempt.status != ENTRANCE_ATTEMPT_ASSIGNED:
            raise CommandError("Entrance assigned fixture did not remain assigned.")

        progress_attempt = assign_attempt(exam, progress_app, self.profiles["entrance-progress"])
        start_attempt(progress_attempt)

        submitted_attempt = assign_attempt(exam, submitted_app, self.profiles["entrance-submitted"])
        start_attempt(submitted_attempt)
        q1, q2 = list(exam.questions.order_by("order"))
        correct_choice = q1.choices.filter(score__gt=0).first()
        submitted_attempt = submit_attempt(
            submitted_attempt,
            [
                AnswerData(question_id=q1.pk, selected_choice_id=correct_choice.pk),
                AnswerData(question_id=q2.pk, written_text="أرغب في خدمة الأسر والتعلم المنهجي."),
            ],
        )
        grade_answer(
            submitted_attempt.answers.get(question=q2),
            Decimal("8.00"),
            "دافع واضح ومناسب.",
            self.staff[GROUP_REVIEWER],
        )

        app = self.applications["application_interview"]
        app.status = PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED
        app.save(update_fields=["status", "updated_at"])
        ProgramApplicationDecision.objects.create(
            program_application=app,
            action=DECISION_ACTION_ENTRANCE_SUBMITTED,
            old_status=PROGRAM_APP_STATUS_SUBMITTED,
            new_status=PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED,
            actor=self.staff[GROUP_ADMIN],
            reason="تم تجهيز الطلب لاختبار مقابلة القبول.",
        )
        request_interview_program_application(
            app,
            self.staff[GROUP_ADMIN],
            interview_at=self.now + timedelta(days=5),
            expires_at=self.now + timedelta(days=10),
            notes="مقابلة قبول هاتفية لاختبار حالة الطلب.",
            send_email=False,
        )

    def _attendance_period(self, cohort, semester):
        return AttendancePeriod.objects.get_or_create(
            cohort=cohort,
            semester=semester,
            defaults={
                "title": "فترة حضور الفصل الدراسي",
                "starts_at": self.now - timedelta(days=5),
                "ends_at": self.now + timedelta(days=5),
                "status": SESSION_STATUS_OPEN,
                "opened_by": self.staff[GROUP_SECRETARY],
                "created_by": self.staff[GROUP_ADMIN],
            },
        )[0]

    def _attendance_session(self, cohort, semester, title, *, status=SESSION_STATUS_OPEN, future=False, cancelled=False, days_ago=3):
        if future:
            open_at = self.now + timedelta(days=3)
            close_at = self.now + timedelta(days=3, hours=2)
            status = SESSION_STATUS_SCHEDULED
        else:
            open_at = self.now - timedelta(days=days_ago)
            close_at = self.now + timedelta(hours=1)
        if cancelled:
            status = SESSION_STATUS_CANCELLED
            close_at = self.now - timedelta(days=days_ago - 1)
        period = self._attendance_period(cohort, semester)
        existing = period.sessions.filter(date=open_at.date()).first()
        if existing:
            return existing
        session = create_attendance_day(
            period,
            session_date=open_at.date(),
            open_at=open_at,
            close_at=close_at,
            actor=self.staff[GROUP_SECRETARY],
            title=title,
        )
        if status != session.status:
            session.status = status
            session.save(update_fields=["status", "updated_at"])
        return session

    def _create_attendance(self):
        secretary = self.staff[GROUP_SECRETARY]
        admin = self.staff[GROUP_ADMIN]
        sem1_d1 = self.graph["semesters"][self.graph["d1"].pk][0]
        sem1_d2 = self.graph["semesters"][self.graph["d2"].pk][0]
        sessions = [
            self._attendance_session(self.graph["cohort_a"], sem1_d1, "جلسة حضور 1 - مغلقة بعد المسح", days_ago=3),
            self._attendance_session(self.graph["cohort_a"], sem1_d1, "جلسة حضور 2 - مغلقة بعد المسح", days_ago=2),
            self._attendance_session(self.graph["cohort_a"], sem1_d1, "جلسة حضور مستقبلية", future=True),
            self._attendance_session(self.graph["cohort_a"], sem1_d1, "جلسة حضور ملغاة", cancelled=True, days_ago=4),
            self._attendance_session(self.graph["cohort_d2"], sem1_d2, "جلسة حضور الدبلوم الثاني"),
        ]
        codes = {}
        for key in ("diploma_online", "diploma_offline", "d2_active"):
            enrollment = self.enrollments[key]
            code = get_or_create_attendance_code_for_enrollment(enrollment, actor=admin)
            codes[key] = build_attendance_qr_payload(code).split("attendance:", 1)[1]

        # Scan every student before closing the shared sessions. This keeps the
        # fixture realistic and avoids trying to scan a second student into a
        # session that was already closed after the first scan.
        for key in ("diploma_online", "diploma_offline"):
            for session in sessions[:2]:
                scan_attendance_token(codes[key], session=session, scanned_by=secretary)
        scan_attendance_token(codes["d2_active"], session=sessions[-1], scanned_by=secretary)
        for session in (sessions[0], sessions[1], sessions[-1]):
            close_attendance_session(session, secretary)

        # Codes for inactive and pending enrollments remain available for the
        # scanner denial tests, but no fake attendance record is created.
        for key in ("expired_diploma", "course_expired", "course_pending"):
            get_or_create_attendance_code_for_enrollment(self.enrollments[key], actor=admin)
        self._attendance_session(self.graph["cohort_a"], sem1_d1, "جلسة اختبار رمز غير صالح", future=True)

    # ------------------------------------------------------------------
    # Assessment attempts, homework, and reviewer queue
    # ------------------------------------------------------------------

    def _create_learning_attempts(self):
        # Course V1: automatic objective quiz released after submission.
        v1_attempt = start_assessment(self.students["course-v1"], self.assessments["course_v1_quiz"])
        v1_answers = []
        for question in self.assessments["course_v1_quiz"].questions.prefetch_related("choices"):
            v1_answers.append(
                AssessmentAnswerData(
                    question_id=question.pk,
                    selected_choice_id=question.choices.filter(is_correct=True).values_list("pk", flat=True).first(),
                )
            )
        v1_attempt = submit_assessment(v1_attempt, v1_answers)
        v1_attempt.refresh_from_db()

        # Course V2: mixed quiz remains under review because its written answer
        # needs a human grade and manual release.
        v2_attempt = start_assessment(self.students["course-v2"], self.assessments["course_v2_quiz"])
        v2_answers = []
        for question in self.assessments["course_v2_quiz"].questions.prefetch_related("choices"):
            if question.question_type == QUESTION_TYPE_MCQ:
                value = {
                    "question_id": question.pk,
                    "selected_choice_id": question.choices.filter(is_correct=True).values_list("pk", flat=True).first(),
                }
            else:
                value = {"question_id": question.pk, "written_text": "أطبق الإصغاء والتدرج في حل المشكلة."}
            v2_answers.append(AssessmentAnswerData(**value))
        submit_assessment(v2_attempt, v2_answers)

        # Diploma online attempt: snapshot, written grading, Admin exam review,
        # but no manual release yet.
        online_attempt = start_assessment(self.students["diploma-online"], self.assessments["exam_a"])
        payload = {}
        for snapshot_question in online_attempt.snapshot_questions.prefetch_related("choices"):
            if snapshot_question.question_type == QUESTION_TYPE_MCQ:
                choice = snapshot_question.choices.filter(is_correct_snapshot=True).first()
                payload[f"q_{snapshot_question.order}"] = choice.pk
            else:
                payload[f"q_{snapshot_question.order}"] = "إجابة تحريرية تجريبية مرتبطة بالمحاضرة."
        online_attempt = submit_diploma_assessment(online_attempt, payload)
        for answer in online_attempt.snapshot_questions.filter(question_type=QUESTION_TYPE_WRITTEN).values_list("answers__pk", "points"):
            grade_diploma_snapshot_answer(
                answer[0], answer[1], "إجابة مكتملة للاختبار.", self.staff[GROUP_REVIEWER],
            )
        online_attempt.refresh_from_db()
        if online_attempt.status == ATTEMPT_STATUS_GRADED:
            admin_review_semester_exam(online_attempt.pk, "pass", actor=self.staff[GROUP_ADMIN], note="مراجعة إدارية مؤجلة للإطلاق.")

        # The online student has a released 14/20 Required Homework result for
        # semester-evaluation previews.
        homework_attempt = AssessmentAttempt.objects.create(
            assessment=self.assessments["homework"],
            student=self.students["diploma-online"],
            attempt_number=1,
            status=ATTEMPT_STATUS_RELEASED,
            started_at=self.now - timedelta(days=3),
            submitted_at=self.now - timedelta(days=2),
            graded_at=self.now - timedelta(days=1),
            graded_by=self.staff[GROUP_ADMIN],
            possible_points_snapshot=self.assessments["homework"].total_points,
            score_points=Decimal("14.00"),
            score_percent=Decimal("70.00"),
            is_pass=True,
            visible_to_student=True,
        )
        HomeworkSubmission.objects.create(
            assessment=self.assessments["homework"],
            student=self.students["diploma-online"],
            attempt=homework_attempt,
            status=HOMEWORK_STATUS_RELEASED,
            submitted_at=self.now - timedelta(days=2),
            score=Decimal("14.00"),
            feedback="درجة منشورة لاختبار تقييم الفصل.",
            reviewed_at=self.now - timedelta(days=1),
            reviewed_by=self.staff[GROUP_ADMIN],
        )

        # Keep the moderator-queue example separate and internally consistent:
        # a submitted attempt, an under-review aggregate, and placeholder-only
        # attachment metadata (never a fabricated real file).
        review_attempt = AssessmentAttempt.objects.create(
            assessment=self.assessments["homework"],
            student=self.students["diploma-offline"],
            attempt_number=1,
            status=ATTEMPT_STATUS_SUBMITTED,
            started_at=self.now - timedelta(days=3),
            submitted_at=self.now - timedelta(days=2),
            possible_points_snapshot=self.assessments["homework"].total_points,
            visible_to_student=False,
        )
        review_submission = HomeworkSubmission.objects.create(
            assessment=self.assessments["homework"],
            student=self.students["diploma-offline"],
            attempt=review_attempt,
            status=HOMEWORK_STATUS_UNDER_REVIEW,
            submitted_at=self.now - timedelta(days=2),
        )
        HomeworkAttachment.objects.create(
            submission=review_submission,
            assessment=self.assessments["homework"],
            student=self.students["diploma-offline"],
            original_filename="ملف-واجب-تجريبي.pdf",
            storage_key="manual-qa/not-a-real-upload/homework.pdf",
            content_type="application/pdf",
            file_size=128000,
            checksum="b" * 64,
            status=HOMEWORK_STATUS_SUBMITTED,
            submitted_at=self.now - timedelta(days=2),
            feedback="ملف تجريبي لاختبار قائمة المراجعة فقط.",
        )

        # Offline Diploma Semester Exam: the student receives a code and the
        # Reviewer enters section totals instead of creating an online attempt.
        offline_code = SemesterExamCode.objects.get(
            assessment=self.assessments["exam_a"],
            diploma_enrollment=self.enrollments["diploma_offline"],
        )
        for section in self.assessments["exam_a"].lecture_sections.order_by("order"):
            OfflineSectionGrade.objects.create(
                exam_code=offline_code,
                section=section,
                score=Decimal("8.00"),
                feedback="درجة قسم أوفلاين تجريبية.",
                graded_by=self.staff[GROUP_REVIEWER],
            )

        # A failed durable submission row is useful for the recovery screen.
        AssessmentSubmission.objects.create(
            submission_id="manual-qa-failed-submission-001",
            attempt=v2_attempt,
            final_payload={"source": SEED_SCOPE, "state": "simulated-worker-failure"},
            accepted_at=self.now - timedelta(minutes=30),
            processing_status="failed",
            failed_at=self.now - timedelta(minutes=20),
            retry_count=2,
            last_error_code="QA_SIMULATED_WORKER_FAILURE",
            last_error_detail="Manual-QA recovery fixture; no real request was lost.",
        )

    # ------------------------------------------------------------------
    # Progression, interviews, direct rejoin, payments, certificates
    # ------------------------------------------------------------------

    def _evaluation(self, enrollment, semester, result, overall, *, attendance, homework, exam, final_result=None, reason=""):
        evaluation = SemesterEvaluation.objects.create(
            diploma_enrollment=enrollment,
            student=enrollment.student,
            cohort=enrollment.cohort,
            semester=semester,
            result=result,
            overall_percent=Decimal(str(overall)),
            attendance_percent=Decimal(str(attendance)) if attendance is not None else None,
            homework_percent=Decimal(str(homework)) if homework is not None else None,
            semester_exam_percent=Decimal(str(exam)) if exam is not None else None,
            config_snapshot={"seed_scope": SEED_SCOPE},
            source_snapshot={"seed_fixture": True},
            finalized_by=self.staff[GROUP_ADMIN],
        )
        outcome = FinalProgressionOutcome.objects.create(
            evaluation=evaluation,
            diploma_enrollment=enrollment,
            student=enrollment.student,
            semester=semester,
            calculated_result=result,
            status="pending",
        )
        if final_result is not None:
            outcome = resolve_final_progression(
                evaluation,
                actor=self.staff[GROUP_ADMIN],
                final_result=final_result,
                reason=reason,
            )
        return evaluation, outcome

    def _failed_source(self, key, student_key, semester):
        enrollment = self.enrollments[key]
        self._evaluation(
            enrollment,
            semester,
            SEMESTER_EVALUATION_FAIL,
            48,
            attendance=70,
            homework=40,
            exam=45,
            final_result=SEMESTER_EVALUATION_FAIL,
            reason="رسوب نهائي محفوظ لاختبار إعادة الالتحاق.",
        )
        return enrollment

    def _create_progression_and_rejoins(self):
        d1_semesters = self.graph["semesters"][self.graph["d1"].pk]
        sem2 = d1_semesters[1]
        sem1 = d1_semesters[0]
        admin = self.staff[GROUP_ADMIN]

        # Active state laboratory for locked, retake, and in-progress rows.
        lab = self.enrollments["status_lab"]
        StudentSemesterProgress.objects.filter(diploma_enrollment=lab, semester=sem1).update(
            status=SEMESTER_PROGRESS_LOCKED,
            locked_at=self.now - timedelta(days=2),
            lock_reason="حالة مقفلة لاختبار صلاحية الفتح.",
            decided_by=admin,
        )
        StudentSemesterProgress.objects.filter(diploma_enrollment=lab, semester=sem2).update(
            status=SEMESTER_PROGRESS_RETAKE_REQUIRED,
            retake_required=True,
            retake_count=1,
            decided_by=admin,
            decision_reason="إعادة تقييم مطلوبة.",
        )
        StudentSemesterProgress.objects.filter(diploma_enrollment=lab, semester=d1_semesters[2]).update(
            status=SEMESTER_PROGRESS_IN_PROGRESS,
        )
        from_status = StudentSemesterProgress.objects.get(diploma_enrollment=lab, semester=sem1)
        from_status.status = SEMESTER_PROGRESS_LOCKED
        from_status.save(update_fields=["status", "updated_at"])
        # An explicit decision row makes the locked state visible in history.
        PromotionDecision.objects.create(
            student=lab.student,
            diploma_enrollment=lab,
            from_semester=sem1,
            decision_type=PROMOTION_DECISION_LOCK,
            previous_status=SEMESTER_PROGRESS_NOT_STARTED,
            new_status=SEMESTER_PROGRESS_LOCKED,
            reason="حالة QA مقفلة.",
            decided_by=admin,
        )
        PromotionDecision.objects.create(
            student=lab.student,
            diploma_enrollment=lab,
            from_semester=sem2,
            decision_type=PROMOTION_DECISION_RETAKE_ASSIGNED,
            previous_status=SEMESTER_PROGRESS_NOT_STARTED,
            new_status=SEMESTER_PROGRESS_RETAKE_REQUIRED,
            reason="حالة QA لإسناد إعادة الاختبار.",
            decided_by=admin,
        )

        # Completed, withdrawn, and pending-final-exam certificate states.
        completed = self.enrollments["completed"]
        for semester in d1_semesters:
            StudentSemesterProgress.objects.filter(diploma_enrollment=completed, semester=semester).update(
                status=SEMESTER_PROGRESS_PASSED_CONFIRMED,
                calculated_score=Decimal("88.00"),
                calculated_at=self.now - timedelta(days=30),
                passed_at=self.now - timedelta(days=30),
                promoted_at=self.now - timedelta(days=25),
                decided_by=admin,
            )
        StudentDiplomaCompletion.objects.create(
            student=completed.student,
            diploma_enrollment=completed,
            status=DIPLOMA_COMPLETION_GRADUATED,
            completed_at=self.now - timedelta(days=20),
            graduated_at=self.now - timedelta(days=10),
            completed_by=admin,
            graduation_notes="حالة تخرج مكتملة بدون ملف PDF مزيف.",
            final_score=Decimal("88.00"),
        )
        withdrawn = self.enrollments["withdrawn"]
        StudentDiplomaCompletion.objects.create(
            student=withdrawn.student,
            diploma_enrollment=withdrawn,
            status=DIPLOMA_COMPLETION_WITHDRAWN,
            completed_by=admin,
            graduation_notes="انسحاب لاختبار صلاحيات السجل التاريخي.",
        )
        StudentDiplomaCompletion.objects.create(
            student=self.profiles["status-lab"],
            diploma_enrollment=lab,
            status=DIPLOMA_COMPLETION_FINAL_EXAM_PENDING,
        )
        StudentDiplomaCompletion.objects.create(
            student=self.profiles["karim"],
            diploma_enrollment=self.enrollments["expired_diploma"],
            status=DIPLOMA_COMPLETION_LOCKED,
        )

        # Direct Rejoin: one plan remains planned, one is active after a
        # one-year cohort change, one is cancelled, and one is expired by the
        # target cohort's finalized cutoff.
        planned_source = self._failed_source("rejoin_planned", "rejoin-planned", sem2)
        planned = create_rejoin_plan(
            self.profiles["rejoin-planned"],
            planned_source,
            sem2,
            actor=admin,
            notes="خطة عودة بعد توقف سنة دراسية.",
        )
        self.rejoin_planned = planned

        active_source = self._failed_source("rejoin_active_source", "rejoin-active", sem2)
        active_plan = create_rejoin_plan(
            self.profiles["rejoin-active"],
            active_source,
            sem2,
            actor=admin,
            notes="عودة مباشرة إلى الفصل المتعثر في الدفعة التالية.",
        )
        self.rejoin_active = activate_rejoin_plan(active_plan, actor=admin)

        cancelled_source = self._failed_source("rejoin_cancelled_source", "rejoin-cancelled", sem2)
        cancelled_plan = create_rejoin_plan(
            self.profiles["rejoin-cancelled"],
            cancelled_source,
            sem2,
            actor=admin,
            notes="خطة ستلغى لاختبار الاسترجاع.",
        )
        cancelled_plan = activate_rejoin_plan(cancelled_plan, actor=admin)
        self.rejoin_cancelled = cancel_rejoin_plan(
            cancelled_plan,
            actor=admin,
            reason="ألغى الطالب العودة قبل بدء الفصل.",
        )
        if self.rejoin_cancelled.status != REJOIN_STATUS_CANCELLED:
            raise CommandError("Cancelled rejoin fixture did not reach cancelled status.")

        missed_source = self._failed_source("rejoin_missed", "rejoin-missed", sem2)
        target_finalized = self.enrollments["target_finalized"]
        self._evaluation(
            target_finalized,
            sem2,
            SEMESTER_EVALUATION_PASS,
            82,
            attendance=90,
            homework=None,
            exam=78,
            final_result=SEMESTER_EVALUATION_PASS,
        )
        try:
            create_rejoin_plan(
                self.profiles["rejoin-missed"],
                missed_source,
                sem2,
                actor=admin,
                notes="يجب رفض العودة بعد إغلاق الفصل في الدفعة التالية.",
            )
        except ValueError as exc:
            self.rejoin_cutoff_error = str(exc)
        else:
            raise CommandError("Finalized target semester incorrectly allowed Direct Rejoin.")

        # Interview states: pending, completed-pass with exceptional final
        # pass, and completed-fail with exceptional final fail.
        pending_eval, _ = self._evaluation(
            self.enrollments["interview_pending"],
            sem1,
            SEMESTER_EVALUATION_PASS,
            66,
            attendance=85,
            homework=55,
            exam=65,
        )
        request_final_interview(pending_eval, actor=admin, notes="مقابلة معلقة قبل القرار النهائي.")

        pass_eval, _ = self._evaluation(
            self.enrollments["interview_pass"],
            sem1,
            SEMESTER_EVALUATION_FAIL,
            55,
            attendance=90,
            homework=45,
            exam=50,
        )
        request_final_interview(pass_eval, actor=admin, notes="مقابلة استثنائية انتهت بالقبول.")
        complete_final_interview(
            pass_eval,
            actor=admin,
            decision=INTERVIEW_DECISION_PASS,
            details="تم قبول الاستمرار بعد مراجعة ظروف الطالب.",
        )
        resolve_final_progression(
            pass_eval,
            actor=admin,
            final_result=SEMESTER_EVALUATION_PASS,
            reason="قرار استثنائي بعد مقابلة ناجحة.",
        )

        fail_eval, _ = self._evaluation(
            self.enrollments["interview_fail"],
            sem1,
            SEMESTER_EVALUATION_PASS,
            64,
            attendance=90,
            homework=60,
            exam=62,
        )
        request_final_interview(fail_eval, actor=admin, notes="مقابلة استثنائية انتهت بالرفض.")
        complete_final_interview(
            fail_eval,
            actor=admin,
            decision=INTERVIEW_DECISION_FAIL,
            details="لم تتحقق شروط الاستمرار بعد المقابلة.",
        )
        resolve_final_progression(
            fail_eval,
            actor=admin,
            final_result=SEMESTER_EVALUATION_FAIL,
            reason="قرار استثنائي بعد مقابلة غير ناجحة.",
        )

    def _create_payments_and_certificates(self):
        admin = self.staff[GROUP_ADMIN]
        # Exercise a paid-to-unpaid correction on a pending enrollment. The
        # payment service correctly refuses to remove the payment dependency
        # from already-active access without a governed rollback.
        course_pending = self.enrollments["course_pending"]
        mark_enrollment_paid(
            course_pending.student,
            "course",
            course_pending.pk,
            admin,
            amount=Decimal("500.00"),
        )
        mark_enrollment_unpaid(
            course_pending.student,
            "course",
            course_pending.pk,
            admin,
            reason="اختبار تغيير حالة الدفع إلى غير مدفوع.",
        )

        grant_payment_waiver(
            self.applications["waiver_active"],
            admin,
            reason="منحة اختبارية معتمدة من الإدارة.",
        )
        revoked_waiver = grant_payment_waiver(
            self.applications["waiver_revoked"],
            admin,
            reason="منحة سيتم سحبها لاختبار الأثر.",
        )
        revoke_payment_waiver(revoked_waiver, admin, reason="إلغاء المنحة بعد مراجعة المستندات.")

        template = CertificateTemplate.objects.create(
            name="قالب شهادة عربي - حالة يدوية",
            diploma=self.graph["d1"],
            language="ar",
            template_type="completion",
            body_html="<p>{{student_name}} - {{diploma_title}}</p>",
            active=True,
            is_placeholder=True,
            created_by=admin,
        )
        completed_enrollment = self.enrollments["completed"]
        self.completed_certificate_enrollment = completed_enrollment
        GeneratedCertificate.objects.create(
            student=self.profiles["withdrawn"],
            diploma_enrollment=self.enrollments["withdrawn"],
            certificate_template=template,
            certificate_number="QA-AR-REVOKED-001",
            status=CERTIFICATE_STATUS_REVOKED,
            generated_by=admin,
            revoked_by=admin,
            revoked_at=self.now - timedelta(days=2),
            revoke_reason="حالة إلغاء شهادة للاختبار.",
            metadata_snapshot={"seed_scope": SEED_SCOPE},
        )
        GeneratedCertificate.objects.create(
            student=self.profiles["status-lab"],
            diploma_enrollment=self.enrollments["status_lab"],
            certificate_template=template,
            certificate_number="QA-AR-FAILED-001",
            status=CERTIFICATE_STATUS_FAILED,
            generated_by=admin,
            metadata_snapshot={"seed_scope": SEED_SCOPE, "reason": "لا يوجد ملف PDF حقيقي في seed."},
        )

    def _create_assignments(self):
        reviewer = self.staff[GROUP_REVIEWER]
        admin = self.staff[GROUP_ADMIN]
        for exam_key in ("exam_a", "d2_exam"):
            exam = self.assessments[exam_key]
            ReviewerAssignment.objects.update_or_create(
                reviewer=reviewer,
                cohort=exam.cohort,
                semester=exam.semester,
                assessment=exam,
                scope=REVIEWER_SCOPE_EXAM,
                cluster=None,
                section=None,
                defaults={
                    "access_mode": REVIEWER_ACCESS_BOTH,
                    "active": True,
                    "assigned_by": admin,
                },
            )

        ReviewerAssignment.objects.create(
            reviewer=reviewer,
            cohort=self.graph["cohort_b"],
            semester=self.graph["semesters"][self.graph["d1"].pk][0],
            scope=REVIEWER_SCOPE_SEMESTER,
            access_mode=REVIEWER_ACCESS_BOTH,
            active=True,
            assigned_by=admin,
        )
        online_attempt = AssessmentAttempt.objects.filter(
            assessment=self.assessments["exam_a"],
            student=self.students["diploma-online"],
        ).first()
        assignment = ReviewerAssignment.objects.filter(
            assessment=self.assessments["exam_a"],
            section__isnull=True,
        ).first()
        section = self.assessments["exam_a"].lecture_sections.order_by("order").first()
        if online_attempt and assignment and section:
            ReviewerExamClaim.objects.create(
                assignment=assignment,
                attempt=online_attempt,
                section=section,
                claimed_by=self.staff[GROUP_REVIEWER],
            )

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def _print_manifest(self):
        self.stdout.write(self.style.SUCCESS("\nArabic manual-QA seed completed.\n"))
        self.stdout.write("All seeded accounts use the shared password: " + self.password)
        self.stdout.write("\nStaff accounts:")
        for role, user in self.staff.items():
            self.stdout.write(f"  - {role}: {user.email}")
        self.stdout.write("\nCore acceptance-review accounts:")
        self.stdout.write("  - ahmad.mansour@test.local -> Diploma 1, submitted")
        self.stdout.write("  - mariam.samir@test.local -> Diploma 2, submitted")
        self.stdout.write("  - youssef.ramy@test.local -> Course, submitted")
        self.stdout.write("  - nour.ashraf@test.local / karim.wael@test.local -> no application")
        self.stdout.write("\nToken URLs for manual public-link checks:")
        for key, token in sorted(self.tokens.items()):
            path = "intake/forms/fill" if key == "intake_open" else "applications/documents/upload"
            self.stdout.write(f"  - {key}: /{path}/{token}/")
        self.stdout.write("\nHigh-value scenario keys:")
        for key, description in self._scenario_manifest():
            self.stdout.write(f"  - {key}: {description}")
        self.stdout.write(
            "\nNot fabricated by design: real uploaded PDFs/images/audio, rendered certificate files, "
            "outgoing email delivery, browser sessions, and concurrency/load evidence."
        )

    def _scenario_manifest(self):
        return [
            ("ADM-01", "ثلاثة طلبات submitted للمراجعة والقبول من لوحة الإدارة"),
            ("ADM-02", "طلب مرفوض وطلب مقابلة وطلب قبول لاختبار انتقالات القرار"),
            ("REG-01", "استمارة مفتوحة برابط عربي ثابت"),
            ("REG-02", "استمارة مكتملة واستجابة محفوظة"),
            ("REG-03", "استمارة منتهية الصلاحية"),
            ("DOC-01", "رابط مستندات مفتوح"),
            ("DOC-02", "رابط مستندات منتهٍ"),
            ("ENT-01", "محاولة دخول assigned"),
            ("ENT-02", "محاولة دخول in-progress"),
            ("ENT-03", "محاولة مكتملة ومصححة لاختبار القبول"),
            ("ASM-01", "نسختا الدورة مع خمس محاضرات لكل نسخة"),
            ("ASM-02", "Quiz تلقائي موضوعي محرر تلقائياً"),
            ("ASM-03", "Quiz مختلط يحتاج تصحيحاً وإطلاقاً يدوياً"),
            ("ASM-04", "امتحان دبلوم قبل الفتح وبعد الإغلاق وإتاحة استثنائية"),
            ("ASM-05", "واجب تحت المراجعة مع metadata لمرفق بلا ملف حقيقي"),
            ("EXAM-01", "امتحان دبلوم online مع snapshot ومراجعة Reviewer/Admin"),
            ("EXAM-02", "امتحان d2_exam مع رموز online/offline وأقسام تصحيح"),
            ("ATT-01", "جلسات حضور مغلقة، مستقبلية، وملغاة"),
            ("ATT-02", "رموز حضور لنشاط active وpending وexpired لاختبار الرفض"),
            ("PAY-01", "دفع ثم تحويل إلى unpaid"),
            ("PAY-02", "منحة دفع نشطة ومنحة مسحوبة"),
            ("PROG-01", "فصول locked وretake_required وin_progress"),
            ("PROG-02", "تخرج، انسحاب، final exam pending وشهادة revoked/failed"),
            ("PROG-20", "واجب مطلوب وآخر اختياري مفقود لاختبار سياسة الإغفال الموروثة والتجاوز"),
            ("PROG-22", "واجب مسجل بنسبة 70% وآخر اختياري من 60 نقطة مفقود لاختبار الإغفال وإعادة الوزن"),
            ("PROG-14", "Rejoin planned بعد رسوب نهائي في الفصل الثاني"),
            ("PROG-15", "Rejoin active في الدفعة التالية بعد توقف سنة"),
            ("PROG-16", "إلغاء Rejoin واستعادة السجل المصدر"),
            ("PROG-17", "Target semester finalized؛ محاولة Rejoin يجب أن ترفض"),
            ("PROG-18", "Interview pending يمنع القرار النهائي"),
            ("PROG-19", "Interview pass/fail مع قرار نهائي استثنائي"),
            ("SEC-01", "Reviewer assignments على مستوى exam/semester وclaim نشط"),
            ("SEC-02", "Moderator cluster scope وSecretary attendance scope"),
            ("STO-01", "شهادة placeholder بلا PDF حقيقي لاختبار مسار التخزين"),
            ("REC-01", "Durable assessment submission في حالة worker failure"),
        ]
