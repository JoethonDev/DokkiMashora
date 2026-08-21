"""Preflight report for legacy/deprecated assessment rows (P2-01).

Reports every assessment row that is not a locked active vocabulary entry
(course-scoped Quiz; diploma-scoped Homework or Semester Exam; one attempt)
together with the durable evidence needed to decide its fate. Read-only:
this command never mutates data.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import Count

from apps.assessments.models import Assessment


class Command(BaseCommand):
    help = "Report deprecated/ambiguous assessment rows with durable evidence (P2-01 preflight)."

    def handle(self, *args, **options):
        rows = list(
            Assessment.objects.select_related(
                "course_version", "semester", "cohort", "created_by",
            ).annotate(
                question_count=Count("questions", distinct=True),
                section_count=Count("lecture_sections", distinct=True),
                attempt_count=Count("attempts", distinct=True),
            ).order_by("assessment_type", "id")
        )

        def scope_label(a: Assessment) -> str:
            if a.course_version_id:
                return f"course (cv={a.course_version_id})"
            if a.semester_id and a.cohort_id:
                return f"diploma (sem={a.semester_id}, cohort={a.cohort_id})"
            return "none"

        self.stdout.write("Assessment legacy preflight (P2-01)")
        self.stdout.write("=" * 78)
        for a in rows:
            self.stdout.write(
                f"[{a.id}] {a.assessment_type:<22} scope={scope_label(a):<28} "
                f"status={a.legacy_status:<22} active={a.is_active}"
            )
            self.stdout.write(
                f"    title={a.title[:60]} | questions={a.question_count} "
                f"sections={a.section_count} attempts={a.attempt_count} "
                f"max_attempts={a.max_attempts}"
            )
            if a.legacy_note:
                self.stdout.write(f"    note: {a.legacy_note}")
        self.stdout.write("=" * 78)
        totals = {
            "active": sum(1 for a in rows if a.legacy_status == "active"),
            "legacy_archived": sum(1 for a in rows if a.legacy_status == "legacy_archived"),
            "legacy_review_required": sum(1 for a in rows if a.legacy_status == "legacy_review_required"),
        }
        self.stdout.write(
            f"Totals: active={totals['active']} "
            f"legacy_archived={totals['legacy_archived']} "
            f"legacy_review_required={totals['legacy_review_required']}"
        )
        for a in rows:
            if a.legacy_status != "active":
                self.stdout.write(
                    self.style.WARNING(
                        f"Remediation needed: assessment {a.id} ({a.title}) - {a.legacy_note}"
                    )
                )
