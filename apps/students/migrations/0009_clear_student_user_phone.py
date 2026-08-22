"""Clear the student User.phone mirror copy.

Phone is single-owner: for students the only owner is StudentProfile.phone
(unique). 0008 previously backfilled student User.phone as a mirror copy so
shared user-level display worked; now that every read uses StudentProfile.phone,
the mirror is removed so a student's phone exists in exactly one place.

This migration sets accounts.User.phone to "" for every user that HAS a
StudentProfile (i.e. students). Staff users have no profile and keep their
User.phone as the single owner. The migration is idempotent: re-running it
finds no remaining non-empty student phones. Reverse is a noop (clearing is
safe and non-destructive — StudentProfile.phone still holds every value).
"""

from django.db import migrations


def clear_student_user_phones(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    qs = (
        User.objects.filter(student_profile__isnull=False)
        .exclude(phone="")
        .order_by("pk")
    )
    for user in qs.iterator(chunk_size=500):
        user.phone = ""
        user.save(update_fields=["phone"])


class Migration(migrations.Migration):
    dependencies = [
        ("students", "0008_backfill_student_user_phone"),
    ]

    operations = [
        migrations.RunPython(clear_student_user_phones, migrations.RunPython.noop),
    ]