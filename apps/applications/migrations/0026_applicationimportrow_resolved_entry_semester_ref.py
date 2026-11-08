from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0025_applicationimport_error_rows_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="applicationimportrow",
            name="resolved_entry_semester_ref",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                verbose_name="resolved entry semester reference",
            ),
        ),
    ]
