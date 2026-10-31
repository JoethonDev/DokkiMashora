from django.db import migrations


def backfill_attempt_graded_by(apps, schema_editor):
    AssessmentAttempt = apps.get_model("assessments", "AssessmentAttempt")
    AssessmentAnswer = apps.get_model("assessments", "AssessmentAnswer")
    AssessmentAttemptSnapshotAnswer = apps.get_model(
        "assessments", "AssessmentAttemptSnapshotAnswer"
    )
    database = schema_editor.connection.alias

    attempts = (
        AssessmentAttempt.objects.using(database)
        .filter(graded_by__isnull=True, status__in=("graded", "released"))
        .iterator()
    )
    for attempt in attempts:
        snapshot_answer = (
            AssessmentAttemptSnapshotAnswer.objects.using(database)
            .filter(
                attempt_question__attempt_id=attempt.pk,
                graded_by__isnull=False,
            )
            .order_by("-graded_at", "-id")
            .values_list("graded_by_id", flat=True)
            .first()
        )
        if snapshot_answer is not None:
            AssessmentAttempt.objects.using(database).filter(pk=attempt.pk).update(
                graded_by_id=snapshot_answer
            )
            continue

        answer = (
            AssessmentAnswer.objects.using(database)
            .filter(attempt_id=attempt.pk, graded_by__isnull=False)
            .order_by("-graded_at", "-id")
            .values_list("graded_by_id", flat=True)
            .first()
        )
        if answer is not None:
            AssessmentAttempt.objects.using(database).filter(pk=attempt.pk).update(
                graded_by_id=answer
            )


class Migration(migrations.Migration):

    dependencies = [
        ("assessments", "0030_preserve_global_grade_visibility"),
    ]

    operations = [
        migrations.RunPython(backfill_attempt_graded_by, migrations.RunPython.noop),
    ]
