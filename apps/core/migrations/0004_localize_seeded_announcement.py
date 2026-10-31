from django.db import migrations


def localize_seeded_announcement(apps, schema_editor):
    Announcement = apps.get_model("core", "LandingAnnouncement")
    for announcement in Announcement.objects.filter(
        title_ar="Registration open",
        title_en="Registration open",
    ):
        announcement.title_ar = "التقديم مفتوح"
        announcement.message_ar = "التقديم متاح الآن من خلال مسار التقديم الرسمي للبرامج."
        announcement.message_en = (
            "Applications are open through the official program application flow."
        )
        announcement.save(update_fields=["title_ar", "message_ar", "message_en"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_announcement_bilingual"),
    ]

    operations = [
        migrations.RunPython(localize_seeded_announcement, migrations.RunPython.noop),
    ]
