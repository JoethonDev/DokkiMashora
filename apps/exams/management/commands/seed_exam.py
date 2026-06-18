"""Seed a sample entrance exam with MCQ and essay questions."""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.exams.models import Choice, Exam, Question


class Command(BaseCommand):
    help = "Seed a sample entrance exam with questions"

    def handle(self, *args, **options):
        now = timezone.now()
        exam, created = Exam.objects.get_or_create(
            title="Entrance Exam - Sample",
            defaults={
                "description": "A sample entrance exam covering Bible knowledge and theology.",
                "duration_minutes": 60,
                "passing_score": Decimal("50.00"),
                "is_active": True,
                "open_at": now,
                "close_at": now + timezone.timedelta(days=365),
            },
        )
        if not created:
            self.stdout.write(self.style.WARNING("Sample exam already exists."))
            return
        q1 = Question.objects.create(
            exam=exam,
            question_type="mcq",
            text="How many books are in the Old Testament?",
            points=Decimal("10.00"),
            order=1,
        )
        Choice.objects.create(question=q1, text="27", is_correct=False, order=1)
        Choice.objects.create(question=q1, text="39", is_correct=True, order=2)
        Choice.objects.create(question=q1, text="66", is_correct=False, order=3)
        Choice.objects.create(question=q1, text="46", is_correct=False, order=4)

        q2 = Question.objects.create(
            exam=exam,
            question_type="mcq",
            text="Who wrote the Gospel of Mark?",
            points=Decimal("10.00"),
            order=2,
        )
        Choice.objects.create(question=q2, text="Matthew", is_correct=False, order=1)
        Choice.objects.create(question=q2, text="Luke", is_correct=False, order=2)
        Choice.objects.create(question=q2, text="John Mark", is_correct=True, order=3)
        Choice.objects.create(question=q2, text="Paul", is_correct=False, order=4)

        q3 = Question.objects.create(
            exam=exam,
            question_type="mcq",
            text="What is the first book of the Bible?",
            points=Decimal("10.00"),
            order=3,
        )
        Choice.objects.create(question=q3, text="Psalms", is_correct=False, order=1)
        Choice.objects.create(question=q3, text="Exodus", is_correct=False, order=2)
        Choice.objects.create(question=q3, text="Genesis", is_correct=True, order=3)
        Choice.objects.create(question=q3, text="Leviticus", is_correct=False, order=4)

        Question.objects.create(
            exam=exam,
            question_type="essay",
            text="Explain the significance of the resurrection in Christian faith.",
            points=Decimal("20.00"),
            order=4,
        )

        Question.objects.create(
            exam=exam,
            question_type="essay",
            text="Describe the role of the Holy Spirit in the life of a believer.",
            points=Decimal("20.00"),
            order=5,
        )

        exam.total_points = Decimal("70.00")
        exam.save(update_fields=["total_points"])

        self.stdout.write(self.style.SUCCESS(f"Created sample exam '{exam.title}' with 5 questions."))
