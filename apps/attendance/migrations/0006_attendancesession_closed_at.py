from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("attendance", "0005_localize_seed_titles"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendancesession",
            name="closed_at",
            field=models.DateTimeField(
                blank=True,
                help_text="The actual time an administrator manually closed this session.",
                null=True,
                verbose_name="closed at",
            ),
        ),
    ]
