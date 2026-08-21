from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("progression", "0010_final_outcome_choice_defaults")]

    operations = [
        migrations.AddConstraint(
            model_name="semesterrejoinplan",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status__in", ["planned", "active"])),
                fields=("student",),
                name="uq_active_rejoin_plan_per_student",
            ),
        ),
    ]
