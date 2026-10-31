"""Seed a diploma lecture MCQ bank and optionally a Semester Exam."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.academics.models import Cohort, Diploma, DiplomaLecture, Semester
from apps.assessments.constants import (
    ASSESSMENT_TYPE_SEMESTER_EXAM,
    GRADE_VISIBILITY_HIDDEN,
    QUESTION_TYPE_MCQ,
    QUESTION_TYPE_WRITTEN,
    RELEASE_MODE_MANUAL,
)
from apps.assessments.models import (
    Assessment,
    AssessmentLectureSection,
    AssessmentSectionManualQuestion,
    DiplomaLectureChoice,
    DiplomaLectureQuestion,
)
from apps.assessments.services import validate_sections_ready

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
    (
        "Which behavior helps a couple resolve a disagreement respectfully?",
        (
            ("Focusing on the specific issue instead of attacking the person", True),
            ("Bringing up every past mistake during the argument", False),
            ("Refusing to listen until the other person agrees", False),
            ("Involving as many relatives as possible", False),
        ),
    ),
    (
        "What is a healthy way to handle differences in family expectations?",
        (
            ("Discuss the expectations openly and agree on realistic limits", True),
            ("Ignore the topic until the wedding day", False),
            ("Let one family decide for both partners", False),
            ("Treat every difference as a reason to end the relationship", False),
        ),
    ),
    (
        "Which action strengthens trust in a relationship?",
        (
            ("Keeping promises and being transparent about important matters", True),
            ("Hiding mistakes to avoid every difficult conversation", False),
            ("Checking private messages without agreement", False),
            ("Making decisions alone and announcing them later", False),
        ),
    ),
    (
        "How should a couple approach financial planning before marriage?",
        (
            ("Discuss income, debts, and shared priorities honestly", True),
            ("Assume money will never be a source of conflict", False),
            ("Keep every financial detail secret", False),
            ("Let one partner carry every financial decision", False),
        ),
    ),
    (
        "Which statement about personal boundaries is correct?",
        (
            ("Boundaries should be discussed and respected by both partners", True),
            ("Boundaries mean refusing all closeness", False),
            ("Only one partner is allowed to set boundaries", False),
            ("Boundaries should never be reviewed as circumstances change", False),
        ),
    ),
    (
        "What is the best response when a concern cannot be resolved alone?",
        (
            ("Seek trusted guidance while keeping both partners involved", True),
            ("Share the concern publicly without consent", False),
            ("Pretend the concern does not exist", False),
            ("Make a permanent decision without discussion", False),
        ),
    ),
)

WRITTEN_PROMPTS = (
    "Explain the main idea of {lecture} and why it matters.",
    "Describe a practical example that applies {lecture}.",
    "Compare two approaches from {lecture} and justify your choice.",
    "Identify a common misunderstanding in {lecture} and correct it.",
    "Summarize the most important takeaway from {lecture} in your own words.",
)


class Command(BaseCommand):
    help = "Seed a diploma lecture MCQ bank and optionally a Semester Exam."

    def add_arguments(self, parser):
        parser.add_argument("--diploma-id", type=int, default=1)
        parser.add_argument(
            "--semester-id",
            type=int,
            help="Exact semester id. When provided, it overrides --diploma-id/--semester-order.",
        )
        parser.add_argument("--semester-order", type=int, default=1)
        parser.add_argument("--cohort-id", type=int, help="Limit seeding and exam scope to one cohort.")
        parser.add_argument(
            "--questions-per-lecture",
            type=int,
            default=10,
            choices=range(1, 11),
        )
        parser.add_argument(
            "--create-exam",
            action="store_true",
            help="Create or refresh the diploma Semester Exam for the selected cohort/semester.",
        )
        parser.add_argument("--exam-title", help="Defaults to the semester and cohort names.")
        parser.add_argument("--passing-score", type=Decimal, default=Decimal("50.00"))
        parser.add_argument("--written-questions", type=int, default=5)
        parser.add_argument("--duration-minutes", type=int, default=90)

    def handle(self, *args, **options):
        questions_per_lecture = options["questions_per_lecture"]
        passing_score = options["passing_score"]
        written_questions = options["written_questions"]
        if not 0 <= passing_score <= 100:
            raise CommandError("Passing score must be between 0 and 100.")
        if written_questions < 0:
            raise CommandError("Written question count cannot be negative.")

        creator = User.objects.filter(is_superuser=True).first() or User.objects.filter(is_staff=True).first()
        if not creator:
            raise CommandError("Create a staff or superuser account before seeding questions.")

        diploma, semester = self._resolve_semester(options)
        cohort = self._resolve_cohort(diploma, options)

        lecture_qs = DiplomaLecture.objects.filter(
            cohort__diploma=diploma,
            semester=semester,
        )
        if cohort is not None:
            lecture_qs = lecture_qs.filter(cohort=cohort)
        lectures = list(lecture_qs.order_by("cohort_id", "order", "id"))
        if not lectures:
            raise CommandError(f"No lectures found for {diploma} / {semester}.")

        question_count = 0
        choice_count = 0
        with transaction.atomic():
            for lecture in lectures:
                for question_text, choices in QUESTION_SETS[:questions_per_lecture]:
                    seeded_text = f"[Seeded Diploma {diploma.id} / Semester {semester.id}] {lecture.title}: {question_text}"
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

            exam = None
            if options["create_exam"]:
                exam = self._create_exam(
                    semester=semester,
                    cohort=cohort,
                    lectures=lectures,
                    creator=creator,
                    questions_per_lecture=questions_per_lecture,
                    passing_score=passing_score,
                    written_questions=written_questions,
                    duration_minutes=options["duration_minutes"],
                    title=options["exam_title"],
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {question_count} MCQ questions and {choice_count} choices "
                f"for {len(lectures)} lecture(s) in {diploma} / {semester}."
            )
        )
        if exam is not None:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Semester Exam id={exam.id} '{exam.title}' is ready: "
                    f"passing={exam.passing_score}%, total={exam.total_points}, "
                    f"written_questions={written_questions}."
                )
            )

    def _resolve_semester(self, options):
        semester_id = options.get("semester_id")
        try:
            if semester_id:
                semester = Semester.objects.select_related("diploma").get(pk=semester_id)
                return semester.diploma, semester
            diploma = Diploma.objects.get(pk=options["diploma_id"])
            semester = Semester.objects.get(
                diploma=diploma,
                order=options["semester_order"],
            )
            return diploma, semester
        except (Diploma.DoesNotExist, Semester.DoesNotExist) as exc:
            raise CommandError(
                "The requested diploma/semester was not found."
            ) from exc

    def _resolve_cohort(self, diploma, options):
        cohort_id = options.get("cohort_id")
        if cohort_id:
            try:
                return Cohort.objects.get(pk=cohort_id, diploma=diploma)
            except Cohort.DoesNotExist as exc:
                raise CommandError(
                    f"Cohort {cohort_id} was not found for {diploma}."
                ) from exc
        if not options["create_exam"]:
            return None

        cohorts = Cohort.objects.filter(diploma=diploma, is_active=True).order_by("id")
        count = cohorts.count()
        if count == 0:
            raise CommandError(f"No active cohort exists for {diploma}.")
        if count > 1:
            raise CommandError(
                "More than one active cohort exists; pass --cohort-id to choose the exam scope."
            )
        return cohorts.first()

    def _create_exam(
        self,
        *,
        semester,
        cohort,
        lectures,
        creator,
        questions_per_lecture,
        passing_score,
        written_questions,
        duration_minutes,
        title,
    ):
        now = timezone.now()
        exam_title = title or f"Semester {semester.title} Exam — {cohort.name}"
        exam, _created = Assessment.objects.update_or_create(
            title=exam_title,
            assessment_type=ASSESSMENT_TYPE_SEMESTER_EXAM,
            semester=semester,
            cohort=cohort,
            defaults={
                "description": "Seeded Semester Exam for the current cohort and semester.",
                "course_version": None,
                "open_at": now,
                "close_at": now + timedelta(days=30),
                "duration_minutes": duration_minutes,
                "total_points": Decimal("0.00"),
                "passing_score": passing_score,
                "max_attempts": 1,
                "is_active": True,
                "grade_visibility": GRADE_VISIBILITY_HIDDEN,
                "release_mode": RELEASE_MODE_MANUAL,
                "allow_late_submission": False,
                "penalty_percent": Decimal("0.00"),
                "created_by": creator,
            },
        )
        if exam.attempts.exists() or exam.exam_codes.exists():
            raise CommandError(
                f"Assessment {exam.id} already has attempts or exam codes; "
                "its section layout cannot be reseeded."
            )

        exam.lecture_sections.all().delete()
        sections = []
        for order, lecture in enumerate(lectures, start=1):
            sections.append(
                AssessmentLectureSection.objects.create(
                    assessment=exam,
                    lecture=lecture,
                    order=order,
                    mcq_count=questions_per_lecture,
                    max_grade=Decimal("0.00"),
                )
            )

        for index in range(written_questions):
            section = sections[index % len(sections)]
            prompt = WRITTEN_PROMPTS[index % len(WRITTEN_PROMPTS)]
            AssessmentSectionManualQuestion.objects.create(
                section=section,
                text=prompt.format(lecture=section.lecture.title),
                question_type=QUESTION_TYPE_WRITTEN,
                points=Decimal("10.00"),
                order=section.manual_questions.count() + 1,
                required=True,
                rubric="Relevant, clear, and supported by the lecture material.",
            )

        total_points = Decimal("0.00")
        for section in sections:
            section.max_grade = section.calculated_max_grade()
            section.save(update_fields=["max_grade", "updated_at"])
            total_points += section.max_grade

        exam.total_points = total_points
        exam.full_clean()
        exam.save()
        try:
            validate_sections_ready(exam)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        return exam
