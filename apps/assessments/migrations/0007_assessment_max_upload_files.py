from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        (
            "assessments",
            "0006_remove_assessment_assessment_requires_course_or_semester_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="assessment",
            name="max_upload_files",
            field=models.PositiveIntegerField(
                default=1,
                help_text="For homework/file assessments: maximum number of files a student may upload.",
                verbose_name="max uploaded files",
            ),
        ),
    ]
