from django.db import migrations, models


def copy_announcement_content(apps, schema_editor):
    Announcement = apps.get_model("core", "LandingAnnouncement")
    LandingPageSettings = apps.get_model("core", "LandingPageSettings")
    settings_page = LandingPageSettings.objects.first()
    content = settings_page.content if settings_page is not None else {}
    ar = content.get("ar", {}) if isinstance(content, dict) else {}
    en = content.get("en", {}) if isinstance(content, dict) else {}

    announcements = list(Announcement.objects.all())
    if not announcements and (ar.get("announcement_title") or en.get("announcement_title")):
        announcements = [Announcement()]

    for announcement in announcements:
        old_title = getattr(announcement, "title", "") or ""
        old_message = getattr(announcement, "message", "") or ""
        old_cta_label = getattr(announcement, "cta_label", "") or ""
        announcement.title_ar = ar.get("announcement_title") or old_title
        announcement.title_en = en.get("announcement_title") or old_title
        announcement.message_ar = ar.get("announcement_body") or old_message
        announcement.message_en = en.get("announcement_body") or old_message
        announcement.cta_label_ar = old_cta_label
        announcement.cta_label_en = old_cta_label
        if announcement.pk:
            announcement.save(
                update_fields=[
                    "title_ar",
                    "title_en",
                    "message_ar",
                    "message_en",
                    "cta_label_ar",
                    "cta_label_en",
                ]
            )
        else:
            announcement.save()

    if settings_page is not None and isinstance(content, dict):
        changed = False
        for language in ("ar", "en"):
            language_content = content.get(language)
            if not isinstance(language_content, dict):
                continue
            for key in ("announcement_title", "announcement_body"):
                if key in language_content:
                    language_content.pop(key, None)
                    changed = True
        if changed:
            settings_page.content = content
            settings_page.save(update_fields=["content"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_landingpagesettings"),
    ]

    operations = [
        migrations.AddField(
            model_name="landingannouncement",
            name="cta_label_ar",
            field=models.CharField(
                blank=True, max_length=80, verbose_name="button label (Arabic)"
            ),
        ),
        migrations.AddField(
            model_name="landingannouncement",
            name="cta_label_en",
            field=models.CharField(
                blank=True, max_length=80, verbose_name="button label (English)"
            ),
        ),
        migrations.AddField(
            model_name="landingannouncement",
            name="message_ar",
            field=models.CharField(
                blank=True, max_length=280, verbose_name="message (Arabic)"
            ),
        ),
        migrations.AddField(
            model_name="landingannouncement",
            name="message_en",
            field=models.CharField(
                blank=True, max_length=280, verbose_name="message (English)"
            ),
        ),
        migrations.AddField(
            model_name="landingannouncement",
            name="title_ar",
            field=models.CharField(
                blank=True, max_length=160, verbose_name="title (Arabic)"
            ),
        ),
        migrations.AddField(
            model_name="landingannouncement",
            name="title_en",
            field=models.CharField(
                blank=True, max_length=160, verbose_name="title (English)"
            ),
        ),
        migrations.RunPython(copy_announcement_content, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="landingannouncement",
            name="cta_label",
        ),
        migrations.RemoveField(
            model_name="landingannouncement",
            name="message",
        ),
        migrations.RemoveField(
            model_name="landingannouncement",
            name="title",
        ),
    ]
