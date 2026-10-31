"""Recompute the stored ``total_score`` for every entrance exam.

``EntranceExam.total_score`` is a derived aggregate of its questions/choices.
Choice-level edits previously left it stale (e.g. a question worth 12 not counted
in the exam total), which skewed grading percentages and pass/fail results. Run
this once to repair data created before choice edits refreshed the total.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.entrance_exams.models import EntranceExam


class Command(BaseCommand):
    help = "Recompute the stored total_score for all entrance exams."

    def handle(self, *args, **options):
        checked = 0
        changed = 0
        for exam in EntranceExam.objects.all().iterator(chunk_size=200):
            checked += 1
            previous = exam.total_score
            exam.recompute_total_score()
            if exam.total_score != previous:
                changed += 1
                self.stdout.write(
                    f"Exam {exam.pk} '{exam.title}': {previous} -> {exam.total_score}"
                )
        self.stdout.write(
            self.style.SUCCESS(
                f"Checked {checked} exam(s); updated {changed} total(s)."
            )
        )
