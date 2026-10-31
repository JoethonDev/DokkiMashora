import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("progression", "0015_finalize_progression_policy_overrides"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="promotiondecision",
            name="superseded_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                null=True,
                verbose_name="superseded at",
            ),
        ),
        migrations.AddField(
            model_name="promotiondecision",
            name="superseded_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="superseded_promotion_decisions",
                to=settings.AUTH_USER_MODEL,
                verbose_name="superseded by",
            ),
        ),
        migrations.AlterField(
            model_name="semesterinterview",
            name="outcome",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="interviews",
                to="progression.finalprogressionoutcome",
                verbose_name="final progression outcome",
            ),
        ),
        migrations.AddField(
            model_name="semesterinterview",
            name="superseded_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                null=True,
                verbose_name="superseded at",
            ),
        ),
        migrations.AddField(
            model_name="semesterinterview",
            name="superseded_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="superseded_semester_interviews",
                to=settings.AUTH_USER_MODEL,
                verbose_name="superseded by",
            ),
        ),
        migrations.AddConstraint(
            model_name="semesterinterview",
            constraint=models.UniqueConstraint(
                condition=models.Q(("superseded_at__isnull", True)),
                fields=("outcome",),
                name="uq_active_interview_per_outcome",
            ),
        ),
    ]
