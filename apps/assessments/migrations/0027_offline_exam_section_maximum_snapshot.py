import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models
from django.db.models import OuterRef, Subquery, Sum


def backfill_exam_code_section_maximums(apps, schema_editor):
    ExamCode = apps.get_model("assessments", "SemesterExamCode")
    SectionMaximum = apps.get_model("assessments", "SemesterExamCodeSectionMaximum")
    database = schema_editor.connection.alias

    batch = []
    rows = ExamCode.objects.using(database).filter(delivery_mode="offline").values_list(
        "pk",
        "assessment__lecture_sections__pk",
        "assessment__lecture_sections__order",
        "assessment__lecture_sections__lecture__title",
        "assessment__lecture_sections__max_grade",
    ).order_by(
        "pk",
        "assessment__lecture_sections__order",
        "assessment__lecture_sections__pk",
    )
    for code_id, section_id, order, title, maximum in rows.iterator(chunk_size=500):
        if section_id is None:
            continue
        batch.append(SectionMaximum(
            exam_code_id=code_id,
            section_id=section_id,
            order_snapshot=order,
            section_title_snapshot=title,
            max_grade_snapshot=maximum,
        ))
        if len(batch) >= 1000:
            SectionMaximum.objects.using(database).bulk_create(batch, batch_size=1000)
            batch.clear()

    if batch:
        SectionMaximum.objects.using(database).bulk_create(batch, batch_size=1000)

    AssessmentAttempt = apps.get_model("assessments", "AssessmentAttempt")
    matching_offline_code_maximum = (
        ExamCode.objects.using(database)
        .filter(
            assessment_id=OuterRef("assessment_id"),
            diploma_enrollment__student__user_id=OuterRef("student_id"),
            delivery_mode="offline",
        )
        .annotate(section_total=Sum("section_maximums__max_grade_snapshot"))
        .order_by("pk")
        .values("section_total")[:1]
    )
    attempts = (
        AssessmentAttempt.objects.using(database)
        .filter(possible_points_snapshot__isnull=True)
        .annotate(offline_maximum=Subquery(matching_offline_code_maximum))
        .filter(offline_maximum__isnull=False)
        .values_list("pk", "offline_maximum")
        .order_by("pk")
    )
    attempt_batch = []
    for attempt_id, maximum in attempts.iterator(chunk_size=500):
        attempt_batch.append(AssessmentAttempt(
            pk=attempt_id,
            possible_points_snapshot=maximum,
        ))
        if len(attempt_batch) >= 500:
            AssessmentAttempt.objects.using(database).bulk_update(
                attempt_batch,
                ["possible_points_snapshot"],
                batch_size=500,
            )
            attempt_batch.clear()

    if attempt_batch:
        AssessmentAttempt.objects.using(database).bulk_update(
            attempt_batch,
            ["possible_points_snapshot"],
            batch_size=500,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("assessments", "0026_stable_attempt_point_maximum"),
    ]

    operations = [
        migrations.CreateModel(
            name="SemesterExamCodeSectionMaximum",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        db_index=True,
                        default=django.utils.timezone.now,
                        editable=False,
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("order_snapshot", models.PositiveIntegerField(verbose_name="section order")),
                ("section_title_snapshot", models.CharField(max_length=255, verbose_name="section title")),
                (
                    "max_grade_snapshot",
                    models.DecimalField(decimal_places=2, max_digits=7, verbose_name="maximum grade"),
                ),
                (
                    "exam_code",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="section_maximums",
                        to="assessments.semesterexamcode",
                        verbose_name="exam code",
                    ),
                ),
                (
                    "section",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="exam_code_maximum_snapshots",
                        to="assessments.assessmentlecturesection",
                        verbose_name="assessment section",
                    ),
                ),
            ],
            options={
                "verbose_name": "exam code section maximum",
                "verbose_name_plural": "exam code section maximums",
                "ordering": ("order_snapshot", "pk"),
            },
        ),
        migrations.AddConstraint(
            model_name="semesterexamcodesectionmaximum",
            constraint=models.UniqueConstraint(
                fields=("exam_code", "section"),
                name="uq_exam_code_section_maximum",
            ),
        ),
        migrations.RunPython(
            backfill_exam_code_section_maximums,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="assessmentattempt",
            name="possible_points_snapshot",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text=(
                    "Maximum possible points frozen for this attempt's online questions or offline section layout."
                ),
                max_digits=7,
                null=True,
                verbose_name="possible points snapshot",
            ),
        ),
    ]
