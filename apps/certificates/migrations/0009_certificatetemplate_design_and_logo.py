from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("certificates", "0008_remove_certificatetemplate_unique_active_template_per_diploma_language_type_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="certificatetemplate",
            name="design",
            field=models.JSONField(blank=True, default=dict, verbose_name="design"),
        ),
        migrations.AddField(
            model_name="certificatetemplate",
            name="logo_storage_key",
            field=models.CharField(blank=True, max_length=500, verbose_name="logo storage key"),
        ),
        migrations.AddField(
            model_name="certificatetemplate",
            name="logo_content_type",
            field=models.CharField(blank=True, max_length=50, verbose_name="logo content type"),
        ),
        migrations.AddField(
            model_name="certificatetemplate",
            name="logo_byte_size",
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name="logo size (bytes)"),
        ),
    ]
