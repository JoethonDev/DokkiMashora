from django.db import migrations, models
from django.db.models import Sum


def backfill_snapshot_maximums(apps, schema_editor):
    AssessmentAttempt = apps.get_model("assessments", "AssessmentAttempt")
    AttemptQuestion = apps.get_model("assessments", "AssessmentAttemptQuestion")
    database = schema_editor.connection.alias
    totals = (
        AttemptQuestion.objects.using(database)
        .values("attempt_id")
        .annotate(possible_points=Sum("points"))
        .order_by("attempt_id")
    )

    batch = []
    for row in totals.iterator(chunk_size=500):
        batch.append(AssessmentAttempt(
            pk=row["attempt_id"],
            possible_points_snapshot=row["possible_points"],
        ))
        if len(batch) == 500:
            AssessmentAttempt.objects.using(database).bulk_update(
                batch,
                ["possible_points_snapshot"],
                batch_size=500,
            )
            batch.clear()

    if batch:
        AssessmentAttempt.objects.using(database).bulk_update(
            batch,
            ["possible_points_snapshot"],
            batch_size=500,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("assessments", "0025_assessment_required_for_semester_evaluation"),
    ]

    operations = [
        migrations.AddField(
            model_name="assessmentattempt",
            name="possible_points_snapshot",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Maximum possible points frozen when the attempt questions are generated.",
                max_digits=7,
                null=True,
                verbose_name="possible points snapshot",
            ),
        ),
        migrations.RunPython(backfill_snapshot_maximums, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="assessmentattemptquestion",
            name="points",
            field=models.DecimalField(decimal_places=2, max_digits=7, verbose_name="points"),
        ),
        migrations.AlterField(
            model_name="assessmentattemptsnapshotanswer",
            name="score",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=7, null=True, verbose_name="score"),
        ),
    ]
