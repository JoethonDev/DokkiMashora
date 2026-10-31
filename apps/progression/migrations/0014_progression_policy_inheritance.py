from django.db import migrations, models


def populate_diploma_policy_defaults(apps, schema_editor):
    DiplomaProgressionRule = apps.get_model("progression", "DiplomaProgressionRule")
    database = schema_editor.connection.alias
    DiplomaProgressionRule.objects.using(database).filter(
        require_all_required_assessments__isnull=True,
    ).update(require_all_required_assessments=True)
    DiplomaProgressionRule.objects.using(database).filter(
        allow_missing_optional_assessments__isnull=True,
    ).update(allow_missing_optional_assessments=False)


class Migration(migrations.Migration):
    dependencies = [
        ("progression", "0013_semesterevaluationconfig_attendance_required_sessions"),
    ]

    operations = [
        migrations.AddField(
            model_name="diplomaprogressionrule",
            name="require_all_required_assessments",
            field=models.BooleanField(
                blank=True,
                null=True,
                verbose_name="require all required assessments",
            ),
        ),
        migrations.AddField(
            model_name="diplomaprogressionrule",
            name="allow_missing_optional_assessments",
            field=models.BooleanField(
                blank=True,
                null=True,
                verbose_name="allow missing optional assessments",
            ),
        ),
        migrations.RunPython(populate_diploma_policy_defaults, migrations.RunPython.noop),
    ]
