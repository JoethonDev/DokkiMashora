import uuid

from django.db import migrations, models


def backfill_access_tokens(apps, schema_editor):
    Exam = apps.get_model("exams", "Exam")
    for exam in Exam.objects.all():
        exam.access_token = uuid.uuid4()
        exam.save(update_fields=["access_token"])


class Migration(migrations.Migration):
    dependencies = [
        ("exams", "0002_add_access_token"),
    ]

    operations = [
        migrations.RunPython(backfill_access_tokens, reverse_code=migrations.RunPython.noop),
        migrations.AlterField(
            model_name="exam",
            name="access_token",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                unique=True,
                verbose_name="access token",
            ),
        ),
    ]
