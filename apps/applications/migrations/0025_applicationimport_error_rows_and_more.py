import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("academics", "0030_course_attendance_enabled_diploma_attendance_enabled"),
        ("applications", "0024_applicationimport_applicationimportrow"),
    ]

    operations = [
        migrations.AddField(
            model_name="applicationimport",
            name="error_rows",
            field=models.JSONField(blank=True, default=list, verbose_name="error rows"),
        ),
        migrations.AddField(
            model_name="applicationimportrow",
            name="resolved_entry_semester",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="application_import_rows",
                to="academics.semester",
                verbose_name="resolved entry semester",
            ),
        ),
        migrations.AddField(
            model_name="applicationimportrow",
            name="errors",
            field=models.JSONField(blank=True, default=list, verbose_name="errors"),
        ),
    ]
