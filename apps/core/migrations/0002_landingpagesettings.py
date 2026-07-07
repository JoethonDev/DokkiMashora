from django.db import migrations, models
from django.utils import timezone

import apps.core.models


class Migration(migrations.Migration):
    dependencies = [("core", "0001_landingannouncement")]

    operations = [
        migrations.CreateModel(
            name="LandingPageSettings",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("created_at", models.DateTimeField(db_index=True, default=timezone.now, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("content", models.JSONField(default=apps.core.models.landing_content_defaults)),
                ("hero_image", models.ImageField(blank=True, upload_to="landing/")),
                ("parenting_image", models.ImageField(blank=True, upload_to="landing/")),
                ("diploma_image", models.ImageField(blank=True, upload_to="landing/")),
                ("show_live_stats", models.BooleanField(default=True)),
                ("stat_value_1", models.CharField(default="12+", max_length=30)),
                ("stat_value_2", models.CharField(default="8+", max_length=30)),
                ("stat_value_3", models.CharField(default="24+", max_length=30)),
                ("stat_value_4", models.CharField(default="100+", max_length=30)),
                ("contact_email", models.EmailField(blank=True, max_length=254)),
                ("contact_phone", models.CharField(blank=True, max_length=40)),
                ("facebook_url", models.URLField(blank=True)),
                ("instagram_url", models.URLField(blank=True)),
                ("youtube_url", models.URLField(blank=True)),
            ],
            options={
                "verbose_name": "landing page settings",
                "verbose_name_plural": "landing page settings",
            },
        )
    ]
