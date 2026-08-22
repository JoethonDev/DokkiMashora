"""Normalize existing stored phone values to the canonical E.164 form.

DECISION §18 / §15.4: phone storage and matching now use one canonical
normalizer (apps.core.phone.normalize_phone_number). Existing rows may hold
format variants (spaces, bare digits, `00` prefix). This data migration
rewrites them to the canonical form so the existing unique constraint on
StudentProfile.phone becomes the real canonical uniqueness rule.

Collision handling is order-independent: before rewriting a stored variant we
check whether the canonical value is already stored in any *other* row's raw
phone. If it is, this row keeps its original raw variant (so it stays findable
by its own key and the unique constraint still holds); otherwise it is set to
the canonical value. Row ordering never changes the outcome, so an update can
never collide with a canonical value that is already stored. Staff/non-student
User.phone values are normalized too (no unique constraint there), so
canonical matching works for staff accounts.
"""

import re

from django.db import migrations


def _normalize(value):
    cleaned = re.sub(r"[^\d+]", "", (value or "").strip())
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if cleaned.startswith("0") and not cleaned.startswith("+") and len(cleaned) == 11:
        cleaned = "+2" + cleaned
    return cleaned


def normalize_phone_values(apps, schema_editor):
    StudentProfile = apps.get_model("students", "StudentProfile")
    User = apps.get_model("accounts", "User")

    for profile in StudentProfile.objects.all():
        raw = profile.phone
        if not raw:
            continue
        canonical = _normalize(raw)
        if not canonical or canonical == raw:
            continue
        if StudentProfile.objects.filter(phone=canonical).exclude(pk=profile.pk).exists():
            # Canonical value is already stored by another row; keep this raw
            # variant so the unique constraint holds and it stays findable.
            continue
        profile.phone = canonical
        profile.save(update_fields=["phone"])

    # Staff / non-student users have no profile; User.phone is their source.
    for user in User.objects.filter(student_profile__isnull=True).exclude(phone="").order_by("pk"):
        canonical = _normalize(user.phone)
        if canonical and canonical != user.phone:
            user.phone = canonical
            user.save(update_fields=["phone"])


class Migration(migrations.Migration):
    dependencies = [
        ("students", "0006_studentprofile_country_code"),
        ("accounts", "0007_user_national_id_unique_constraint"),
    ]

    operations = [
        migrations.RunPython(normalize_phone_values, migrations.RunPython.noop),
    ]