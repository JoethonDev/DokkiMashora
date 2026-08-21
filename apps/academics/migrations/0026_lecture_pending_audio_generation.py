from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("academics", "0025_diplomalecture_audio_duration_seconds")]

    operations = [
        migrations.AddField(
            model_name="lecture",
            name="audio_pending_storage_key",
            field=models.CharField(
                blank=True,
                max_length=500,
                verbose_name="pending audio storage key",
            ),
        ),
        migrations.AddField(
            model_name="diplomalecture",
            name="audio_pending_storage_key",
            field=models.CharField(
                blank=True,
                max_length=500,
                verbose_name="pending audio storage key",
            ),
        ),
    ]
