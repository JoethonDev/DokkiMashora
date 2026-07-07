"""Backfill youtube_video_id for lectures that have youtube_url but empty video_id."""

import re

from django.db import migrations

_YT_VIDEO_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?.*?v=|embed/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)


def backfill_video_ids(apps, schema_editor):
    Lecture = apps.get_model("academics", "Lecture")
    for lecture in Lecture.objects.filter(youtube_url__isnull=False).exclude(youtube_url="").filter(youtube_video_id=""):
        m = _YT_VIDEO_ID_RE.search(lecture.youtube_url)
        if m:
            lecture.youtube_video_id = m.group(1)
            lecture.save(update_fields=["youtube_video_id"])


def reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("academics", "0016_alter_courseversion_label"),
    ]

    operations = [
        migrations.RunPython(backfill_video_ids, reverse),
    ]
