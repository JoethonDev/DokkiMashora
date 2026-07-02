import uuid

from django.db import migrations, models


def backfill_access_tokens(apps, schema_editor):
    EntranceExam = apps.get_model("entrance_exams", "EntranceExam")
    for exam in EntranceExam.objects.all():
        exam.access_token = uuid.uuid4()
        exam.save(update_fields=["access_token"])


class Migration(migrations.Migration):

    dependencies = [
        ("entrance_exams", "0010_remove_entranceattempt_unique_attempt_per_student_exam_cohort_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="entranceexam",
            name="access_token",
            field=models.UUIDField(default=uuid.uuid4, editable=False, verbose_name="access token"),
        ),
        migrations.RunPython(backfill_access_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="entranceexam",
            name="access_token",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name="access token"),
        ),
    ]
