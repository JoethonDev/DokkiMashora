"""Seed sample entrance exams with MCQ and written questions."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.academics.models import Cohort, Diploma
from apps.entrance_exams.models import EntranceChoice, EntranceExam, EntranceQuestion

User = get_user_model()


class Command(BaseCommand):
    help = "Seed sample entrance exams with questions"

    def handle(self, *args, **options):
        admin = User.objects.filter(is_superuser=True).first()
        if not admin:
            self.stdout.write(self.style.ERROR("No superuser found. Create one first."))
            return

        diploma = Diploma.objects.filter(is_active=True).first()
        if not diploma:
            self.stdout.write(self.style.ERROR("No active diploma found. Create one first."))
            return

        cohort = Cohort.objects.filter(diploma=diploma, is_active=True).first()
        if not cohort:
            self.stdout.write(self.style.ERROR(f"No active cohort found for diploma '{diploma.title}'. Create one first."))
            return

        now = timezone.now()
        exam_data: dict[str, Any] = {
            "title": f"Entrance Exam — {diploma.title}",
            "description": "Sample entrance assessment covering theological foundations.",
            "diploma": diploma,
            "cohort": cohort,
            "duration_minutes": 60,
            "passing_score": Decimal("50.00"),
            "is_active": True,
            "open_at": now,
            "close_at": now + timezone.timedelta(days=90),
            "created_by": admin,
        }

        exam, created = EntranceExam.objects.get_or_create(
            title=exam_data["title"],
            cohort=cohort,
            defaults=exam_data,
        )
        if not created:
            self.stdout.write(self.style.WARNING(f"Entrance exam '{exam.title}' already exists."))
            return

        questions_data = [
            {
                "text": "How many books are in the Old Testament?",
                "question_type": "mcq",
                "points": Decimal("10.00"),
                "order": 1,
                "choices": [
                    ("27", False, 1), ("39", True, 2), ("66", False, 3), ("46", False, 4),
                ],
            },
            {
                "text": "Who wrote the Gospel of Mark?",
                "question_type": "mcq",
                "points": Decimal("10.00"),
                "order": 2,
                "choices": [
                    ("Matthew", False, 1), ("Luke", False, 2),
                    ("John Mark", True, 3), ("Paul", False, 4),
                ],
            },
            {
                "text": "What is the first book of the Bible?",
                "question_type": "mcq",
                "points": Decimal("10.00"),
                "order": 3,
                "choices": [
                    ("Psalms", False, 1), ("Exodus", False, 2),
                    ("Genesis", True, 3), ("Leviticus", False, 4),
                ],
            },
            {
                "text": "Explain the significance of the resurrection in Christian faith.",
                "question_type": "written",
                "points": Decimal("20.00"),
                "order": 4,
                "choices": [],
            },
            {
                "text": "Describe the role of the Holy Spirit in the life of a believer.",
                "question_type": "written",
                "points": Decimal("20.00"),
                "order": 5,
                "choices": [],
            },
        ]

        for q_data in questions_data:
            choices = q_data.pop("choices", [])
            question = EntranceQuestion.objects.create(exam=exam, **q_data)
            for text, is_correct, order in choices:
                EntranceChoice.objects.create(
                    question=question, text=text, is_correct=is_correct, order=order,
                )

        exam.refresh_from_db()
        self.stdout.write(
            self.style.SUCCESS(
                f"Created entrance exam '{exam.title}' with "
                f"{exam.questions.count()} questions ({exam.total_points} pts)."
            )
        )
