"""Ensure a student has viewable time-tracked course and diploma content.

This command is additive and idempotent. It reuses the student's existing
active Diploma enrollment, enables video tracking on that Diploma, and
creates/updates a dedicated demo Course with an active enrollment.
It never deletes rows, creates users, or resets passwords.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.academics.constants import (
    ENROLLMENT_STATUS_ACTIVE,
    YOUTUBE_METADATA_STATUS_READY,
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
    Semester,
)
from apps.students.models import StudentProfile

YOUTUBE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
YOUTUBE_VIDEO_ID = "dQw4w9WgXcQ"
YOUTUBE_DURATION_SECONDS = 212
TRACKING_PERCENT = 80
DEMO_COURSE_TITLE = "Time Tracking Demo Course"


class Command(BaseCommand):
    help = (
        "Enable video tracking and ensure published YouTube content for a "
        "student's active Diploma and a dedicated demo Course."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Required. Confirms that demo content should be created/updated.",
        )
        parser.add_argument(
            "--student-email",
            default="",
            help="Student account to target. Defaults to the first active Student.",
        )
        parser.add_argument(
            "--lectures",
            type=int,
            default=3,
            help=(
                "Minimum number of published lectures for the student's active "
                "diploma semester and the demo course (default: 3)."
            ),
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not options["confirm"]:
            raise CommandError("Refusing to seed without the explicit --confirm flag.")
        lecture_count = options["lectures"]
        if not 1 <= lecture_count <= 20:
            raise CommandError("--lectures must be between 1 and 20.")

        admin = (
            get_user_model()
            .objects.filter(is_superuser=True, is_active=True)
            .order_by("pk")
            .first()
        )
        if admin is None:
            raise CommandError("No active superuser is available to own seeded content.")

        profile = self._student_profile(options["student_email"])
        diploma_enrollment = (
            DiplomaEnrollment.objects.select_related("cohort__diploma", "entry_semester")
            .filter(student=profile, status=ENROLLMENT_STATUS_ACTIVE)
            .order_by("pk")
            .first()
        )
        if diploma_enrollment is None:
            raise CommandError(
                f"{profile.user.email} has no active Diploma enrollment. "
                "Activate an enrollment first, then rerun this command."
            )

        diploma = diploma_enrollment.cohort.diploma
        semester = self._semester(diploma_enrollment)
        diploma_lectures = self._ensure_diploma_lectures(
            diploma_enrollment.cohort,
            semester,
            lecture_count,
        )
        self._enable_diploma_tracking(diploma)

        course, course_enrollment, course_lectures = self._ensure_course(
            profile,
            admin,
            lecture_count,
        )

        self.stdout.write(self.style.SUCCESS("Time-tracking demo content is ready."))
        self.stdout.write(f"Student: {profile.user.email}")
        self.stdout.write(
            f"Diploma: {diploma.title} | tracking="
            f"{diploma.video_tracking_enabled} ({diploma.video_completion_percent}%)"
        )
        self.stdout.write(
            f"  Cohort: {diploma_enrollment.cohort.name} | semester: {semester.title}"
        )
        for lecture in diploma_lectures:
            self.stdout.write(
                f"  Diploma lecture {lecture.order}: {lecture.title} "
                f"({lecture.youtube_duration_seconds}s)"
            )
        self.stdout.write(
            "  Open: "
            + reverse(
                "academics:student_diploma_lecture_view",
                args=[diploma_enrollment.pk, semester.pk, diploma_lectures[0].pk],
            )
        )
        self.stdout.write(
            f"Course: {course.title} | enrollment={course_enrollment.status} | "
            f"tracking={course.video_tracking_enabled} ({course.video_completion_percent}%)"
        )
        for lecture in course_lectures:
            self.stdout.write(
                f"  Course lecture {lecture.order}: {lecture.title} "
                f"({lecture.youtube_duration_seconds}s)"
            )
        self.stdout.write(
            "  Open: "
            + reverse(
                "academics:student_lecture_view",
                args=[course_enrollment.course_version_id, course_lectures[0].pk],
            )
        )
        self.stdout.write(
            "Re-running this command updates the same rows and does not duplicate content."
        )

    def _student_profile(self, email: str) -> StudentProfile:
        profiles = StudentProfile.objects.select_related("user").filter(user__is_active=True)
        if email:
            profiles = profiles.filter(user__email__iexact=email.strip())
        profile = profiles.order_by("pk").first()
        if profile is None:
            target = f" for {email}" if email else ""
            raise CommandError(f"No active student profile found{target}.")
        return profile

    def _semester(self, enrollment: DiplomaEnrollment) -> Semester:
        if (
            enrollment.entry_semester_id
            and enrollment.entry_semester.diploma_id == enrollment.cohort.diploma_id
            and enrollment.entry_semester.is_active
        ):
            return enrollment.entry_semester
        semester = (
            Semester.objects.filter(diploma=enrollment.cohort.diploma, is_active=True)
            .order_by("order", "pk")
            .first()
        )
        if semester is None:
            raise CommandError(
                f"{enrollment.cohort.diploma.title} has no active semester to attach content to."
            )
        return semester

    def _enable_diploma_tracking(self, diploma: Diploma) -> None:
        update_fields = []
        if not diploma.video_tracking_enabled:
            diploma.video_tracking_enabled = True
            update_fields.append("video_tracking_enabled")
        if not diploma.video_completion_percent:
            diploma.video_completion_percent = TRACKING_PERCENT
            update_fields.append("video_completion_percent")
        if update_fields:
            diploma.save(update_fields=[*update_fields, "updated_at"])

    def _ensure_diploma_lectures(
        self,
        cohort: Cohort,
        semester: Semester,
        count: int,
    ) -> list[DiplomaLecture]:
        lectures = list(
            DiplomaLecture.objects.filter(cohort=cohort, semester=semester).order_by("order", "pk")
        )
        for lecture in lectures:
            self._apply_youtube_metadata(lecture)
        next_order = max((lecture.order for lecture in lectures), default=0) + 1
        while len(lectures) < count:
            lecture = DiplomaLecture.objects.create(
                cohort=cohort,
                semester=semester,
                order=next_order,
                title=f"Time Tracking Demo Lecture {next_order}",
                description="Seeded lecture for validating video progress end to end.",
                youtube_url=YOUTUBE_URL,
                youtube_video_id=YOUTUBE_VIDEO_ID,
                youtube_duration_seconds=YOUTUBE_DURATION_SECONDS,
                youtube_metadata_status=YOUTUBE_METADATA_STATUS_READY,
                release_at=timezone.now() - timedelta(days=1),
                is_published=True,
            )
            lectures.append(lecture)
            next_order += 1
        return lectures[:count]

    def _ensure_course(
        self,
        profile: StudentProfile,
        admin,
        count: int,
    ) -> tuple[Course, CourseEnrollment, list[Lecture]]:
        course, _ = Course.objects.update_or_create(
            title=DEMO_COURSE_TITLE,
            defaults={
                "description": "Seeded course for validating video progress end to end.",
                "is_active": True,
                "video_tracking_enabled": True,
                "video_completion_percent": TRACKING_PERCENT,
                "created_by": admin,
            },
        )
        version, _ = CourseVersion.objects.update_or_create(
            course=course,
            label="v1",
            defaults={
                "access_duration_days": 3650,
                "is_active": True,
                "created_by": admin,
            },
        )
        enrollment, _ = CourseEnrollment.objects.update_or_create(
            student=profile,
            course_version=version,
            defaults={
                "status": ENROLLMENT_STATUS_ACTIVE,
                "activated_at": timezone.now(),
                "expires_at": None,
            },
        )
        lectures = []
        for order in range(1, count + 1):
            lecture, _ = Lecture.objects.update_or_create(
                course_version=version,
                order=order,
                defaults={
                    "title": f"Time Tracking Demo Course Lecture {order}",
                    "description": "Seeded lecture for validating video progress end to end.",
                    "youtube_url": YOUTUBE_URL,
                    "youtube_video_id": YOUTUBE_VIDEO_ID,
                    "youtube_duration_seconds": YOUTUBE_DURATION_SECONDS,
                    "youtube_metadata_status": YOUTUBE_METADATA_STATUS_READY,
                    "release_at": timezone.now() - timedelta(days=1),
                    "is_published": True,
                },
            )
            lectures.append(lecture)
        return course, enrollment, lectures

    def _apply_youtube_metadata(self, lecture: DiplomaLecture) -> None:
        update_fields = []
        if not lecture.youtube_url:
            lecture.youtube_url = YOUTUBE_URL
            update_fields.append("youtube_url")
        if not lecture.youtube_video_id:
            lecture.youtube_video_id = YOUTUBE_VIDEO_ID
            update_fields.append("youtube_video_id")
        if not lecture.youtube_duration_seconds:
            lecture.youtube_duration_seconds = YOUTUBE_DURATION_SECONDS
            update_fields.append("youtube_duration_seconds")
        if lecture.youtube_metadata_status != YOUTUBE_METADATA_STATUS_READY:
            lecture.youtube_metadata_status = YOUTUBE_METADATA_STATUS_READY
            update_fields.append("youtube_metadata_status")
        if not lecture.is_published:
            lecture.is_published = True
            update_fields.append("is_published")
        if lecture.release_at is None or lecture.release_at > timezone.now():
            lecture.release_at = timezone.now() - timedelta(days=1)
            update_fields.append("release_at")
        if update_fields:
            lecture.save(update_fields=[*update_fields, "updated_at"])
