import uuid

from django.db import migrations, models


def backfill_access_token(apps, schema_editor):
    EntranceExam = apps.get_model("entrance_exams", "EntranceExam")
    for exam in EntranceExam.objects.filter(access_token__isnull=True):
        exam.access_token = uuid.uuid4()
        exam.save(update_fields=["access_token"])


def backfill_cohort(apps, schema_editor):
    EntranceAttempt = apps.get_model("entrance_exams", "EntranceAttempt")
    for attempt in EntranceAttempt.objects.filter(cohort__isnull=True).select_related("program_application", "exam"):
        cohort_id = None
        if attempt.program_application_id and attempt.program_application.cohort_id:
            cohort_id = attempt.program_application.cohort_id
        elif attempt.exam_id and attempt.exam.cohort_id:
            cohort_id = attempt.exam.cohort_id
        if cohort_id is not None:
            attempt.cohort_id = cohort_id
            attempt.save(update_fields=["cohort"])


class Migration(migrations.Migration):
    dependencies = [
        ("entrance_exams", "0007_remove_is_correct"),
    ]

    operations = [
        migrations.AddField(
            model_name="entranceexam",
            name="access_token",
            field=models.UUIDField(
                default=uuid.uuid4,
                null=True,
                editable=False,
                verbose_name="access token",
            ),
        ),
        migrations.AddField(
            model_name="entranceexam",
            name="valid_until",
            field=models.DateTimeField(blank=True, null=True, verbose_name="valid until"),
        ),
        migrations.AddField(
            model_name="entranceexam",
            name="revoked_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="revoked at"),
        ),
        migrations.AddField(
            model_name="entranceattempt",
            name="cohort",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.PROTECT,
                related_name="entrance_attempts",
                to="academics.cohort",
                verbose_name="cohort",
            ),
        ),
        migrations.RunPython(backfill_access_token, migrations.RunPython.noop),
        migrations.RunPython(backfill_cohort, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="entranceexam",
            name="access_token",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                unique=True,
                verbose_name="access token",
            ),
        ),
        migrations.AddConstraint(
            model_name="entranceattempt",
            constraint=models.UniqueConstraint(
                condition=models.Q(cohort__isnull=False),
                fields=("student", "exam", "cohort"),
                name="unique_attempt_per_student_exam_cohort",
            ),
        ),
    ]
