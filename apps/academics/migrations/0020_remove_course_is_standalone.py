from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("academics", "0019_migrate_diploma_lecture_data"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="course",
            name="is_standalone",
        ),
        migrations.RemoveConstraint(
            model_name="courseversion",
            name="uq_standalone_course_version_label",
        ),
        migrations.AddConstraint(
            model_name="courseversion",
            constraint=models.UniqueConstraint(
                condition=models.Q(("cohort__isnull", True)),
                fields=("course", "label"),
                name="uq_course_version_label",
            ),
        ),
    ]
