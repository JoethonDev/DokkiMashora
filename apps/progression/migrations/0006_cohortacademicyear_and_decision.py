from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("progression", "0005_alter_promotiondecision_decision_type_and_more")]

    operations = [
        migrations.CreateModel(
            name="CohortAcademicYear",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("year_number", models.PositiveIntegerField(verbose_name="year number")),
                ("start_date", models.DateField(verbose_name="start date")),
                ("end_date", models.DateField(verbose_name="end date")),
                ("semesters_per_year", models.PositiveIntegerField(default=2, verbose_name="semesters per year")),
                ("cohort", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="academic_years", to="academics.cohort", verbose_name="cohort")),
            ],
            options={"ordering": ("cohort", "year_number")},
        ),
        migrations.AddConstraint(
            model_name="cohortacademicyear",
            constraint=models.UniqueConstraint(fields=("cohort", "year_number"), name="uq_cohort_academic_year"),
        ),
        migrations.AlterField(
            model_name="promotiondecision",
            name="decision_type",
            field=models.CharField(choices=[
                ("auto_promote", "Auto promote"), ("manual_promote", "Manual promote"),
                ("demote", "Demote"), ("override_pass", "Override pass"), ("override_fail", "Override fail"),
                ("lock", "Lock"), ("unlock", "Unlock"), ("retake_assigned", "Retake assigned"),
                ("retake_cleared", "Retake cleared"), ("graduate", "Graduate"), ("complete", "Complete"),
                ("rejoin_planned", "Rejoin planned"), ("rejoin_activated", "Rejoin activated"),
                ("rejoin_cancelled", "Rejoin cancelled"), ("request_interview", "Request interview"),
                ("interview_pass", "Interview pass"), ("interview_fail", "Interview fail"),
                ("confirm_pass", "Confirm pass"), ("academic_year_promote", "Academic year promote"),
            ], max_length=25, db_index=True, verbose_name="decision type"),
        ),
    ]
