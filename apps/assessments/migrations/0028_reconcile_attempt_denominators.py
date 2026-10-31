from django.db import migrations
from django.db.models import OuterRef, Subquery, Sum


def reconcile_attempt_denominators(apps, schema_editor):
    AssessmentAttempt = apps.get_model("assessments", "AssessmentAttempt")
    ExamCode = apps.get_model("assessments", "SemesterExamCode")
    database = schema_editor.connection.alias

    # Online attempts are authoritative from their immutable question snapshot,
    # even if a legacy roster code currently reports another delivery mode.
    online_attempts = (
        AssessmentAttempt.objects.using(database)
        .filter(snapshot_questions__isnull=False)
        .values("pk", "possible_points_snapshot")
        .annotate(snapshot_total=Sum("snapshot_questions__points"))
        .order_by("pk")
        .values_list("pk", "possible_points_snapshot", "snapshot_total")
    )
    batch = []
    for attempt_id, current_maximum, maximum in online_attempts.iterator(chunk_size=500):
        if maximum is None or current_maximum == maximum:
            continue
        batch.append(AssessmentAttempt(pk=attempt_id, possible_points_snapshot=maximum))
        if len(batch) >= 500:
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

    offline_maximum = (
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
    offline_attempts = (
        AssessmentAttempt.objects.using(database)
        .filter(
            snapshot_questions__isnull=True,
            status="graded",
            score_points__isnull=False,
            submitted_at__isnull=False,
        )
        .annotate(offline_maximum=Subquery(offline_maximum))
        .filter(offline_maximum__isnull=False)
        .order_by("pk")
        .values_list("pk", "possible_points_snapshot", "offline_maximum")
    )
    batch = []
    for attempt_id, current_maximum, maximum in offline_attempts.iterator(chunk_size=500):
        if current_maximum == maximum:
            continue
        batch.append(AssessmentAttempt(pk=attempt_id, possible_points_snapshot=maximum))
        if len(batch) >= 500:
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
        ("assessments", "0027_offline_exam_section_maximum_snapshot"),
    ]

    operations = [
        migrations.RunPython(reconcile_attempt_denominators, migrations.RunPython.noop),
    ]
