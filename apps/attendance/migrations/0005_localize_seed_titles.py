from django.db import migrations


def localize_known_seed_titles(apps, schema_editor):
    AttendancePeriod = apps.get_model("attendance", "AttendancePeriod")
    AttendanceSession = apps.get_model("attendance", "AttendanceSession")

    AttendancePeriod.objects.filter(title="Attendance period").update(title="فترة الحضور")
    AttendancePeriod.objects.filter(title="Seed Attendance Period").update(title="فترة حضور تجريبية")
    AttendanceSession.objects.filter(title="Seed Attendance Session 1").update(
        title="جلسة حضور تجريبية 1"
    )
    AttendanceSession.objects.filter(title="Seed Attendance Session 2").update(
        title="جلسة حضور تجريبية 2"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("attendance", "0004_normalize_period_status"),
    ]

    operations = [
        migrations.RunPython(localize_known_seed_titles, migrations.RunPython.noop),
    ]
