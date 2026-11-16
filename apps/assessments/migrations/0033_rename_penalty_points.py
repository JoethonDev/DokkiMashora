from decimal import Decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("assessments", "0032_assessment_is_completion_exam_and_more"),
    ]

    operations = [
        migrations.RenameField(
            model_name="assessment",
            old_name="penalty_percent",
            new_name="penalty_points",
        ),
        migrations.AlterField(
            model_name="assessment",
            name="penalty_points",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                help_text="Points deducted from the total for each wrong MCQ answer. 0 means no penalty.",
                max_digits=5,
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(100),
                ],
                verbose_name="penalty points",
            ),
        ),
    ]
