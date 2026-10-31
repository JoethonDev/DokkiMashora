from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("assessments", "0024_reviewer_assignment_logical_scope"),
    ]

    operations = [
        migrations.AddField(
            model_name="assessment",
            name="is_required_for_semester_evaluation",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "If absent, this assessment may block the semester result depending on the diploma or semester progression policy."
                ),
                verbose_name="Required for semester evaluation",
            ),
        ),
        migrations.AddConstraint(
            model_name="assessment",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        course_version__isnull=True,
                        semester__isnull=False,
                        cohort__isnull=False,
                        assessment_type__in=("homework", "semester_exam"),
                    )
                    | models.Q(is_required_for_semester_evaluation=True)
                ),
                name="check_asmt_optional_scope",
            ),
        ),
    ]
