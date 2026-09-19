from django.db import migrations
from django.utils import timezone


def normalize_period_status(apps, schema_editor):
    AttendancePeriod = apps.get_model("attendance", "AttendancePeriod")
    now = timezone.now()
    for period in AttendancePeriod.objects.exclude(status="cancelled"):
        if period.starts_at <= now < period.ends_at and period.status == "scheduled":
            period.status = "open"
            period.save(update_fields=["status", "updated_at"])
        elif now >= period.ends_at and period.status in {"scheduled", "open"}:
            period.status = "closed"
            period.save(update_fields=["status", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("attendance", "0003_attendance_period_constraints"),
    ]

    operations = [
        migrations.RunPython(normalize_period_status, migrations.RunPython.noop),
    ]
