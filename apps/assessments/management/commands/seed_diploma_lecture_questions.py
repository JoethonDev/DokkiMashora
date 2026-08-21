"""Seed a small MCQ bank for the first diploma semester lectures."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.academics.models import Diploma, DiplomaLecture, Semester
from apps.assessments.constants import QUESTION_TYPE_MCQ
from apps.assessments.models import DiplomaLectureChoice, DiplomaLectureQuestion


User = get_user_model()

QUESTION_SETS = (
    (
        "Which practice best supports healthy communication during engagement?",
        (
            ("Active listening and honest discussion", True),
            ("Avoiding every difficult topic", False),
            ("Using relatives to speak for both partners", False),
            ("Keeping expectations unspoken", False),
        ),
    ),
    (
        "What is the main purpose of discussing expectations before marriage?",
        (
            ("To build shared understanding and identify differences early", True),
            ("To ensure that only one partner makes decisions", False),
            ("To postpone every important decision", False),
            ("To avoid learning about each other's priorities", False),
        ),
    ),
    (
        "Which response demonstrates active listening in a difficult conversation?",
        (
            ("Reflecting what was heard before responding", True),
            ("Interrupting to prove a point", False),
            ("Changing the subject immediately", False),
            ("Assuming the other person's intention", False),
        ),
    ),
    (
        "Which is an example of a healthy boundary in an engagement relationship?",
        (
            ("Agreeing together how personal information and time are shared", True),
            ("Demanding access to every private conversation", False),
            ("Refusing to discuss boundaries", False),
            ("Allowing others to make every relationship decision", False),
        ),
    ),
)


class Command(BaseCommand):
    help = "Seed MCQ questions for Diploma 1 Semester 1 lectures"

    def add_arguments(self, parser):
        parser.add_argument("--diploma-id", type=int, default=1)
        parser.add_argument("--semester-order", type=int, default=1)
        parser.add_argument("--questions-per-lecture", type=int, default=4, choices=range(3, 6))

    def handle(self, *args, **options):
        diploma_id = options["diploma_id"]
        semester_order = options["semester_order"]
        questions_per_lecture = options["questions_per_lecture"]
        creator = User.objects.filter(is_superuser=True).first() or User.objects.filter(is_staff=True).first()
        if not creator:
            raise CommandError("Create a staff or superuser account before seeding questions.")

        try:
            diploma = Diploma.objects.get(pk=diploma_id)
            semester = Semester.objects.get(diploma=diploma, order=semester_order)
        except (Diploma.DoesNotExist, Semester.DoesNotExist) as exc:
            raise CommandError(
                f"Diploma {diploma_id} with semester order {semester_order} was not found."
            ) from exc

        lectures = list(
            DiplomaLecture.objects.filter(
                cohort__diploma=diploma,
                semester=semester,
            ).order_by("cohort_id", "order", "id")
        )
        if not lectures:
            raise CommandError(f"No lectures found for {diploma} / {semester}.")

        question_count = 0
        choice_count = 0
        with transaction.atomic():
            for lecture in lectures:
                for question_text, choices in QUESTION_SETS[:questions_per_lecture]:
                    seeded_text = f"[Seeded Diploma {diploma_id} / Semester {semester_order}] {lecture.title}: {question_text}"
                    question, _ = DiplomaLectureQuestion.objects.update_or_create(
                        lecture=lecture,
                        text=seeded_text,
                        defaults={
                            "question_type": QUESTION_TYPE_MCQ,
                            "points": Decimal("5.00"),
                            "active": True,
                            "created_by": creator,
                        },
                    )
                    question_count += 1
                    for order, (choice_text, is_correct) in enumerate(choices, start=1):
                        DiplomaLectureChoice.objects.update_or_create(
                            question=question,
                            order=order,
                            defaults={"text": choice_text, "is_correct": is_correct},
                        )
                        choice_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {question_count} MCQ questions and {choice_count} choices "
                f"for {len(lectures)} lecture(s) in {diploma} / {semester}."
            )
        )
