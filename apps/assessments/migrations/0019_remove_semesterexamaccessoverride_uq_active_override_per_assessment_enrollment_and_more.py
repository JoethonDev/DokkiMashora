import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("academics", "0026_lecture_pending_audio_generation"),
        ("assessments", "0018_semester_exam_admin_review"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="semesterexamaccessoverride",
            name="uq_active_override_per_assessment_enrollment",
        ),
        migrations.AddField(
            model_name="semesterexamaccessoverride",
            name="course_enrollment",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="course_assessment_access_overrides",
                to="academics.courseenrollment",
                verbose_name="course enrollment",
            ),
        ),
        migrations.AlterField(
            model_name="semesterexamaccessoverride",
            name="diploma_enrollment",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="semester_exam_access_overrides",
                to="academics.diplomaenrollment",
                verbose_name="diploma enrollment",
            ),
        ),
        migrations.AddIndex(
            model_name="semesterexamaccessoverride",
            index=models.Index(
                fields=["course_enrollment", "status"],
                name="assessments_course__77b3b2_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="semesterexamaccessoverride",
            constraint=models.UniqueConstraint(
                condition=models.Q(status="active", diploma_enrollment__isnull=False),
                fields=("assessment", "diploma_enrollment"),
                name="uq_active_override_per_assessment_diploma",
            ),
        ),
        migrations.AddConstraint(
            model_name="semesterexamaccessoverride",
            constraint=models.UniqueConstraint(
                condition=models.Q(status="active", course_enrollment__isnull=False),
                fields=("assessment", "course_enrollment"),
                name="uq_active_override_per_assessment_course",
            ),
        ),
        migrations.AddConstraint(
            model_name="semesterexamaccessoverride",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(diploma_enrollment__isnull=False, course_enrollment__isnull=True)
                    | models.Q(diploma_enrollment__isnull=True, course_enrollment__isnull=False)
                ),
                name="ck_override_exactly_one_enrollment",
            ),
        ),
    ]
