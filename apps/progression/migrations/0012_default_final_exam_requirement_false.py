from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("progression", "0011_unique_active_rejoin_plan_per_student")]

    operations = [
        migrations.AlterField(
            model_name="diplomaprogressionrule",
            name="require_final_exam",
            field=models.BooleanField(default=False, verbose_name="require final exam"),
        ),
    ]
