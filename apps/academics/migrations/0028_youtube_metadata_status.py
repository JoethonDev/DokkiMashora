from django.db import migrations, models
from django.utils.translation import gettext_lazy as _


def backfill_youtube_metadata_status(apps, schema_editor):
    database = schema_editor.connection.alias
    for model_name in ("Lecture", "DiplomaLecture"):
        model = apps.get_model("academics", model_name)
        rows = model.objects.using(database)
        rows.filter(
            youtube_video_id__gt="",
            youtube_duration_seconds__gt=0,
        ).update(youtube_metadata_status="ready")
        rows.filter(
            youtube_video_id__gt="",
            youtube_duration_seconds__isnull=True,
            youtube_metadata_fetched_at__isnull=True,
        ).update(youtube_metadata_status="pending")
        rows.filter(
            youtube_video_id__gt="",
            youtube_duration_seconds__isnull=True,
            youtube_metadata_fetched_at__isnull=False,
        ).update(youtube_metadata_status="unavailable")
        rows.filter(
            youtube_video_id="",
            youtube_url__gt="",
        ).update(youtube_metadata_status="unavailable")
        rows.filter(
            youtube_video_id__gt="",
            youtube_duration_seconds=0,
        ).update(youtube_metadata_status="unavailable")


class Migration(migrations.Migration):
    dependencies = [
        ("academics", "0027_alter_moderatorassignment_course_version"),
    ]

    operations = [
        migrations.AddField(
            model_name="lecture",
            name="youtube_metadata_status",
            field=models.CharField(
                blank=True,
                choices=[
                    ("pending", _("Checking video details")),
                    ("ready", _("Video details ready")),
                    ("unavailable", _("Video details unavailable")),
                ],
                default="",
                max_length=16,
                verbose_name=_("YouTube metadata status"),
            ),
        ),
        migrations.AddField(
            model_name="lecture",
            name="youtube_metadata_request_id",
            field=models.UUIDField(
                blank=True,
                editable=False,
                null=True,
                verbose_name=_("YouTube metadata request ID"),
            ),
        ),
        migrations.AddField(
            model_name="diplomalecture",
            name="youtube_metadata_status",
            field=models.CharField(
                blank=True,
                choices=[
                    ("pending", _("Checking video details")),
                    ("ready", _("Video details ready")),
                    ("unavailable", _("Video details unavailable")),
                ],
                default="",
                max_length=16,
                verbose_name=_("YouTube metadata status"),
            ),
        ),
        migrations.AddField(
            model_name="diplomalecture",
            name="youtube_metadata_request_id",
            field=models.UUIDField(
                blank=True,
                editable=False,
                null=True,
                verbose_name=_("YouTube metadata request ID"),
            ),
        ),
        migrations.RunPython(backfill_youtube_metadata_status, migrations.RunPython.noop),
    ]
