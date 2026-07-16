"""Seed academic structure: diploma, semester, course, cohort, lectures."""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.academics.models import (
    Cohort,
    Course,
    Diploma,
    DiplomaLecture,
    Semester,
)

User = get_user_model()


class Command(BaseCommand):
    help = "Seed a diploma with semesters, courses, cohorts, and lectures"

    def handle(self, *args, **options):
        admin = User.objects.filter(is_superuser=True).first()
        if not admin:
            self.stdout.write(self.style.ERROR("No superuser found. Create one first."))
            return

        diploma, created = Diploma.objects.get_or_create(
            title="Diploma in Theological Studies",
            defaults={
                "description": "A comprehensive program covering Bible, theology, and church history.",
                "total_semesters": 2,
                "created_by": admin,
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created diploma '{diploma.title}'"))
        else:
            self.stdout.write(self.style.WARNING("Diploma already exists."))

        sem1, _ = Semester.objects.get_or_create(
            diploma=diploma,
            order=1,
            defaults={"title": "First Semester"},
        )
        sem2, _ = Semester.objects.get_or_create(
            diploma=diploma,
            order=2,
            defaults={"title": "Second Semester"},
        )

        old_testament, _ = Course.objects.get_or_create(
            title="Old Testament Survey",
            defaults={"description": "An overview of the Old Testament books and themes.", "created_by": admin},
        )
        new_testament, _ = Course.objects.get_or_create(
            title="New Testament Survey",
            defaults={"description": "An overview of the New Testament books and themes.", "created_by": admin},
        )
        theology, _ = Course.objects.get_or_create(
            title="Systematic Theology",
            defaults={"description": "Core doctrines of the Christian faith.", "created_by": admin},
        )
        church_history, _ = Course.objects.get_or_create(
            title="Church History",
            defaults={"description": "History of the Christian church from Pentecost to today.", "created_by": admin},
        )

        cohort, created = Cohort.objects.get_or_create(
            diploma=diploma,
            name="2026 Spring",
            defaults={
                "start_date": date(2026, 1, 15),
                "end_date": date(2026, 12, 15),
                "created_by": admin,
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created cohort '{cohort.name}'"))
        else:
            self.stdout.write(self.style.WARNING("Cohort already exists."))

        # ponytail: diploma content uses DiplomaLecture only; CourseVersion+Lecture is for standalone courses.
        # Diploma lectures are created below.

        # ponytail: 2 DiplomaLectures per semester, add more if sample content needs variety
        now = timezone.now()
        for sem, sem_name in [(sem1, "First"), (sem2, "Second")]:
            for i in range(1, 3):
                dl, dl_created = DiplomaLecture.objects.get_or_create(
                    cohort=cohort,
                    semester=sem,
                    order=i,
                    defaults={
                        "title": f"Diploma Lecture {i}: {sem_name} Semester - Part {i}",
                        "description": f"Description for diploma lecture {i} of {sem_name} semester.",
                        "is_published": True,
                        "release_at": now - timedelta(days=(2 - i) * 7),
                    },
                )
                if dl_created:
                    self.stdout.write(f"  Created DiplomaLecture {dl.order}: {dl.title}")

        self.stdout.write(self.style.SUCCESS("Academic structure seeded successfully."))
