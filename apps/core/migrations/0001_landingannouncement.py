# Generated manually because manage.py is deleted in the current worktree.

import django.utils.timezone
from django.db import migrations, models


def seed_default_announcement(apps, schema_editor):
    LandingAnnouncement = apps.get_model("core", "LandingAnnouncement")
    LandingAnnouncement.objects.get_or_create(
        title="Registration open",
        defaults={
            "message": "Applications are open through the official program application flow.",
            "is_active": True,
        },
    )


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="LandingAnnouncement",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        db_index=True,
                        default=django.utils.timezone.now,
                        editable=False,
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("title", models.CharField(max_length=160, verbose_name="title")),
                ("message", models.CharField(max_length=280, verbose_name="message")),
                (
                    "cta_label",
                    models.CharField(
                        blank=True,
                        max_length=80,
                        verbose_name="button label",
                    ),
                ),
                (
                    "cta_url",
                    models.CharField(
                        blank=True,
                        max_length=240,
                        verbose_name="button URL",
                    ),
                ),
                (
                    "starts_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name="starts at",
                    ),
                ),
                (
                    "ends_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name="ends at",
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        db_index=True,
                        default=True,
                        verbose_name="active",
                    ),
                ),
            ],
            options={
                "verbose_name": "landing announcement",
                "verbose_name_plural": "landing announcements",
                "ordering": ("-updated_at", "-created_at"),
            },
        ),
        migrations.RunPython(seed_default_announcement, migrations.RunPython.noop),
    ]
