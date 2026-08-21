from django.db import migrations


def normalize_activation_template(apps, schema_editor):
    EmailTemplate = apps.get_model("communications", "EmailTemplate")
    for template in EmailTemplate.objects.filter(slug="activation"):
        body = template.body.replace(
            " and your initial password is {{ password }}",
            "",
        ).replace("{{ password }}", "")
        subject = template.subject.replace("{{ password }}", "")
        if body != template.body or subject != template.subject:
            template.body = body
            template.subject = subject
            template.save(update_fields=["body", "subject", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("communications", "0014_alter_bulkemailsend_status"),
    ]

    operations = [
        migrations.RunPython(normalize_activation_template, migrations.RunPython.noop),
    ]
