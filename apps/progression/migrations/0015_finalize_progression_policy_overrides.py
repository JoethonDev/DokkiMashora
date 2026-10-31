from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("progression", "0014_progression_policy_inheritance"),
    ]

    operations = [
        migrations.AlterField(
            model_name="diplomaprogressionrule",
            name="require_all_required_assessments",
            field=models.BooleanField(
                default=True,
                verbose_name="require all required assessments",
            ),
        ),
        migrations.AlterField(
            model_name="diplomaprogressionrule",
            name="allow_missing_optional_assessments",
            field=models.BooleanField(
                default=False,
                verbose_name="allow missing optional assessments",
            ),
        ),
        migrations.AlterField(
            model_name="semesterprogressionrule",
            name="pass_percent",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=5,
                null=True,
                validators=[MinValueValidator(0), MaxValueValidator(100)],
                verbose_name="pass percent",
            ),
        ),
        migrations.AlterField(
            model_name="semesterprogressionrule",
            name="require_all_required_assessments",
            field=models.BooleanField(
                blank=True,
                default=None,
                null=True,
                verbose_name="require all required assessments",
            ),
        ),
        migrations.AlterField(
            model_name="semesterprogressionrule",
            name="allow_missing_optional_assessments",
            field=models.BooleanField(
                blank=True,
                default=None,
                null=True,
                verbose_name="allow missing optional assessments",
            ),
        ),
    ]
