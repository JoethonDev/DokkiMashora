from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("attendance", "0002_attendanceperiod_and_dated_sessions"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="attendanceperiod",
            constraint=models.UniqueConstraint(
                fields=("cohort", "semester"),
                name="uq_attendance_period_cohort_semester",
            ),
        ),
        migrations.AddConstraint(
            model_name="attendancesession",
            constraint=models.CheckConstraint(
                condition=models.Q(period__isnull=True)
                | models.Q(session_number__isnull=False),
                name="ck_period_session_has_number",
            ),
        ),
        migrations.AddConstraint(
            model_name="attendancesession",
            constraint=models.UniqueConstraint(
                condition=models.Q(period__isnull=False),
                fields=("period", "session_number"),
                name="uq_attendance_period_session_number",
            ),
        ),
        migrations.AddConstraint(
            model_name="attendancesession",
            constraint=models.UniqueConstraint(
                condition=models.Q(period__isnull=False),
                fields=("period", "date"),
                name="uq_attendance_period_session_date",
            ),
        ),
    ]
