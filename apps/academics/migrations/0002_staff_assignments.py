# Generated manually because the local Python runtime is unavailable.

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("academics", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TeacherCohortAssignment",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        db_index=True,
                        default=django.utils.timezone.now,
                        editable=False,
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("active", models.BooleanField(db_index=True, default=True, verbose_name="active")),
                ("revoked_at", models.DateTimeField(blank=True, null=True, verbose_name="revoked at")),
                ("revoke_reason", models.TextField(blank=True, verbose_name="revoke reason")),
                (
                    "assigned_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="teacher_assignments_created",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="assigned by",
                    ),
                ),
                (
                    "cohort",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="teacher_assignments",
                        to="academics.cohort",
                        verbose_name="cohort",
                    ),
                ),
                (
                    "revoked_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="teacher_assignments_revoked",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="revoked by",
                    ),
                ),
                (
                    "teacher",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="teacher_cohort_assignments",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="teacher",
                    ),
                ),
            ],
            options={
                "verbose_name": "teacher cohort assignment",
                "verbose_name_plural": "teacher cohort assignments",
                "ordering": ("-created_at",),
            },
        ),
        migrations.CreateModel(
            name="ModeratorStudentAssignment",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        db_index=True,
                        default=django.utils.timezone.now,
                        editable=False,
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("active", models.BooleanField(db_index=True, default=True, verbose_name="active")),
                ("revoked_at", models.DateTimeField(blank=True, null=True, verbose_name="revoked at")),
                ("revoke_reason", models.TextField(blank=True, verbose_name="revoke reason")),
                (
                    "assigned_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="moderator_assignments_created",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="assigned by",
                    ),
                ),
                (
                    "cohort",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="moderator_assignments",
                        to="academics.cohort",
                        verbose_name="cohort",
                    ),
                ),
                (
                    "moderator",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="moderator_student_assignments",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="moderator",
                    ),
                ),
                (
                    "revoked_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="moderator_assignments_revoked",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="revoked by",
                    ),
                ),
                (
                    "student",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="moderator_assignments",
                        to="students.studentprofile",
                        verbose_name="student",
                    ),
                ),
            ],
            options={
                "verbose_name": "moderator student assignment",
                "verbose_name_plural": "moderator student assignments",
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddIndex(
            model_name="teachercohortassignment",
            index=models.Index(fields=["teacher", "active"], name="academics_t_teacher_9f55dd_idx"),
        ),
        migrations.AddIndex(
            model_name="teachercohortassignment",
            index=models.Index(fields=["cohort", "active"], name="academics_t_cohort__18a7b5_idx"),
        ),
        migrations.AddIndex(
            model_name="moderatorstudentassignment",
            index=models.Index(fields=["moderator", "active"], name="academics_m_moderat_4cc5b6_idx"),
        ),
        migrations.AddIndex(
            model_name="moderatorstudentassignment",
            index=models.Index(fields=["student", "cohort", "active"], name="academics_m_student_6de9e6_idx"),
        ),
        migrations.AddConstraint(
            model_name="teachercohortassignment",
            constraint=models.UniqueConstraint(
                condition=models.Q(("active", True)),
                fields=("teacher", "cohort"),
                name="unique_active_teacher_cohort_assignment",
            ),
        ),
        migrations.AddConstraint(
            model_name="moderatorstudentassignment",
            constraint=models.UniqueConstraint(
                condition=models.Q(("active", True)),
                fields=("moderator", "student", "cohort"),
                name="unique_active_moderator_student_cohort_assignment",
            ),
        ),
    ]
