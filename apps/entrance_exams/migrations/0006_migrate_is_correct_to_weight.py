from decimal import Decimal

from django.db import migrations


def migrate_is_correct_to_weight(apps, schema_editor):
    EntranceChoice = apps.get_model("entrance_exams", "EntranceChoice")
    EntranceChoice.objects.filter(is_correct=True).exclude(weight__gt=Decimal("0.00")).update(weight=Decimal("1.00"))


class Migration(migrations.Migration):
    dependencies = [
        ("entrance_exams", "0005_entrancechoice_weight_and_more"),
    ]

    operations = [
        migrations.RunPython(migrate_is_correct_to_weight, migrations.RunPython.noop),
    ]
