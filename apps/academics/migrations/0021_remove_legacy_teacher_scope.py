from django.db import migrations, models


def remove_legacy_course_scopes(apps, schema_editor):
    TeacherAssignment = apps.get_model("academics", "TeacherAssignment")
    TeacherAssignment.objects.filter(scope_type="standalone_course").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("academics", "0020_remove_course_is_standalone"),
    ]

    operations = [
        migrations.RunPython(remove_legacy_course_scopes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="teacherassignment",
            name="scope_type",
            field=models.CharField(
                choices=[
                    ("whole_diploma", "Whole diploma"),
                    ("diploma_semester", "Diploma semester"),
                    ("diploma_cohort", "Diploma cohort"),
                    ("semester_cohort", "Semester within cohort"),
                    ("course_version", "Course version"),
                ],
                db_index=True,
                max_length=24,
                verbose_name="scope type",
            ),
        ),
    ]
