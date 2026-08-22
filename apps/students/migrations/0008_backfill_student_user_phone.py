"""Backfill student User.phone to the canonical StudentProfile.phone value.

The mirror is now written with the same canonical value everywhere. Existing
student rows may still hold a pre-canonical format in User.phone (e.g. spaces
or a bare leading 0). This additive data migration syncs them to the profile's
canonical value so shared user-level search/display stays consistent.
"""

from django.db import migrations


def backfill_student_user_phones(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for user in User.objects.filter(student_profile__isnull=False).exclude(
        student_profile__phone=""
    ).select_related("student_profile"):
        canonical = user.student_profile.phone
        if user.phone != canonical:
            user.phone = canonical
            user.save(update_fields=["phone"])


class Migration(migrations.Migration):
    dependencies = [
        ("students", "0007_normalize_phone"),
    ]

    operations = [
        migrations.RunPython(backfill_student_user_phones, migrations.RunPython.noop),
    ]