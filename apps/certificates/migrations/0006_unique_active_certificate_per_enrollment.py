from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("certificates", "0005_certificatetemplate_field_mapping_and_more")]

    operations = [
        migrations.AddConstraint(
            model_name="generatedcertificate",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("diploma_enrollment__isnull", False),
                    ("status__in", ["generated", "available", "regeneration_required"]),
                ),
                fields=("diploma_enrollment",),
                name="uq_active_certificate_per_enrollment",
            ),
        ),
    ]
