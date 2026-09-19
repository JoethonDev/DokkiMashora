"""Pre-create assigned diploma exam attempts and immutable snapshots."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.assessments.constants import (
    ASSESSMENT_TYPE_SEMESTER_EXAM,
    ATTEMPT_STATUS_ASSIGNED,
    ATTEMPT_STATUS_IN_PROGRESS,
)
from apps.assessments.models import Assessment, AssessmentAttempt
from apps.assessments.services import generate_diploma_assessment_snapshot


class Command(BaseCommand):
    help = "Pre-create assigned Semester Exam attempts and snapshots for student IDs."

    def add_arguments(self, parser):
        parser.add_argument("assessment_id", type=int)
        parser.add_argument("--student-id", dest="student_ids", action="append", type=int)
        parser.add_argument("--stress-users", action="store_true", help="Target stress0001..stressN users.")
        parser.add_argument("--users", type=int, default=400, help="Stress users to target.")

    def handle(self, *args, **options):
        assessment = Assessment.objects.get(pk=options["assessment_id"])
        if assessment.assessment_type != ASSESSMENT_TYPE_SEMESTER_EXAM:
            raise CommandError("The assessment must be a Semester Exam.")
        if not assessment.semester_id or not assessment.cohort_id or assessment.course_version_id:
            raise CommandError("The assessment must be a diploma-scoped Semester Exam.")

        user_model = get_user_model()
        if not options["student_ids"] and not options["stress_users"]:
            raise CommandError("Provide --student-id or --stress-users.")
        students = user_model.objects.filter(pk__in=options["student_ids"] or [])
        if options["stress_users"]:
            students = user_model.objects.filter(
                email__regex=r"^stress\d{4}@dokkimashora\.test$",
            ).order_by("pk")[: options["users"]]
        created = 0
        skipped = 0
        for student in students:
            with transaction.atomic():
                locked = AssessmentAttempt.objects.select_for_update().filter(
                    assessment=assessment, student=student,
                )
                if locked.filter(status=ATTEMPT_STATUS_IN_PROGRESS).exists():
                    skipped += 1
                    continue
                assigned = locked.filter(status=ATTEMPT_STATUS_ASSIGNED).first()
                if assigned:
                    skipped += 1
                    continue
                attempt_number = locked.count() + 1
                if attempt_number > assessment.max_attempts:
                    skipped += 1
                    continue
                attempt = AssessmentAttempt.objects.create(
                    assessment=assessment,
                    student=student,
                    attempt_number=attempt_number,
                    status=ATTEMPT_STATUS_ASSIGNED,
                )
                generate_diploma_assessment_snapshot(attempt)
                created += 1
        requested_ids = set(options["student_ids"] or [])
        missing = requested_ids - set(user_model.objects.filter(pk__in=requested_ids).values_list("pk", flat=True))
        self.stdout.write(
            self.style.SUCCESS(
                f"Pre-created {created} attempt(s); skipped {skipped}; missing users {len(missing)}."
            )
        )
