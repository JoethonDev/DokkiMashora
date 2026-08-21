from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("academics", "0024_lecture_audio_duration_seconds_lectureaudioprogress")]

    operations = [
        migrations.AddField(
            model_name="diplomalecture",
            name="audio_duration_seconds",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Server-measured audio duration persisted during transcode",
                null=True,
                verbose_name="audio duration (seconds)",
            ),
        ),
    ]
