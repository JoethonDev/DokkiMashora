import django.core.validators
from django.db import migrations, models


def migrate_is_correct_to_score(apps, schema_editor):
    EntranceChoice = apps.get_model("entrance_exams", "EntranceChoice")
    for choice in EntranceChoice.objects.filter(is_correct=True).select_related("question"):
        choice.score = choice.question.points
        choice.save(update_fields=["score"])


class Migration(migrations.Migration):
    dependencies = [
        (
            "entrance_exams",
            "0010_remove_entranceattempt_unique_attempt_per_student_exam_cohort_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="entrancechoice",
            name="score",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                max_digits=5,
                validators=[django.core.validators.MinValueValidator(0)],
                verbose_name="score",
            ),
        ),
        migrations.RunPython(migrate_is_correct_to_score, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="entrancechoice",
            name="is_correct",
        ),
    ]
