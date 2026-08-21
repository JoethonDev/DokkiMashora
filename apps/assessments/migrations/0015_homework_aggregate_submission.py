"""P2-02: recast per-file homework rows as attachments and add the aggregate submission.

Non-destructive: the legacy per-file table is RENAMED to ``HomeworkAttachment``
(all rows and columns preserved), then a new aggregate ``HomeworkSubmission``
table is created and populated from legacy groups. Every historical file row
remains linked; conflicting/multi-attempt groups are marked
``legacy_review_required`` and never carry an authoritative score. No storage
object is moved inside this migration.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

LEGACY_STATUS_ACTIVE = "active"
LEGACY_STATUS_REVIEW_REQUIRED = "legacy_review_required"


def create_aggregates_from_legacy_attachments(apps, schema_editor):
    HomeworkSubmission = apps.get_model("assessments", "HomeworkSubmission")
    HomeworkAttachment = apps.get_model("assessments", "HomeworkAttachment")
    Assessment = apps.get_model("assessments", "Assessment")
    AssessmentAttempt = apps.get_model("assessments", "AssessmentAttempt")

    def _derive_status(attempt_status):
        return {
            "submitted": "under_review",
            "under_review": "under_review",
            "graded": "graded",
            "released": "released",
        }.get(attempt_status, "uploaded")

    groups = {}
    for attachment in HomeworkAttachment.objects.all().order_by("created_at"):
        groups.setdefault((attachment.assessment_id, attachment.student_id), []).append(attachment)

    for (assessment_id, student_id), attachments in groups.items():
        attempt_ids = {a.attempt_id for a in attachments if a.attempt_id}
        scored = [(a.score, a.feedback, a.reviewed_by_id) for a in attachments if a.score is not None]
        unambiguous = len(attempt_ids) <= 1 and len(scored) <= 1

        assessment = Assessment.objects.filter(pk=assessment_id).first()
        assessment_legacy = assessment is not None and assessment.legacy_status != LEGACY_STATUS_ACTIVE
        review_required = (not unambiguous) or assessment_legacy

        canonical_attempt_id = next(iter(attempt_ids)) if len(attempt_ids) == 1 else None
        canonical_attempt_status = None
        if canonical_attempt_id is not None:
            canonical_attempt_status = AssessmentAttempt.objects.filter(
                pk=canonical_attempt_id
            ).values_list("status", flat=True).first()

        aggregate = HomeworkSubmission.objects.create(
            assessment_id=assessment_id,
            student_id=student_id,
            attempt_id=canonical_attempt_id,
            status=_derive_status(canonical_attempt_status) if not review_required else "under_review",
            submitted_at=attachments[0].submitted_at,
            score=scored[0][0] if len(scored) == 1 and not review_required else None,
            feedback=scored[0][1] if len(scored) == 1 and not review_required else "",
            reviewed_by_id=scored[0][2] if len(scored) == 1 and not review_required else None,
            legacy_status=LEGACY_STATUS_REVIEW_REQUIRED if review_required else LEGACY_STATUS_ACTIVE,
            legacy_note=(
                "Legacy homework files were reconciled into one aggregate submission "
                "preserved read-only."
                if review_required else ""
            ),
        )
        HomeworkAttachment.objects.filter(
            pk__in=[a.pk for a in attachments]
        ).update(submission_id=aggregate.pk)


class Migration(migrations.Migration):
    dependencies = [
        ("assessments", "0014_add_active_vocabulary_constraints"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RenameModel(
            old_name="HomeworkSubmission",
            new_name="HomeworkAttachment",
        ),
        migrations.AlterModelOptions(
            name="homeworkattachment",
            options={
                "ordering": ("-created_at",),
                "verbose_name": "homework attachment",
                "verbose_name_plural": "homework attachments",
            },
        ),
        migrations.AlterField(
            model_name="homeworkattachment",
            name="status",
            field=models.CharField(
                choices=[
                    ("uploaded", "Uploaded"),
                    ("submitted", "Submitted"),
                    ("under_review", "Under review"),
                    ("accepted", "Accepted"),
                    ("needs_revision", "Needs revision"),
                    ("rejected", "Rejected"),
                    ("graded", "Graded"),
                    ("released", "Released"),
                ],
                db_index=True,
                default="uploaded",
                help_text="Legacy per-file status preserved for reconciliation.",
                max_length=20,
                verbose_name="status",
            ),
        ),
        migrations.AlterField(
            model_name="homeworkattachment",
            name="score",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Legacy per-file score preserved for reconciliation; the authoritative score lives on the aggregate submission.",
                max_digits=5,
                null=True,
                verbose_name="legacy score",
            ),
        ),
        migrations.AlterField(
            model_name="homeworkattachment",
            name="feedback",
            field=models.TextField(blank=True, verbose_name="legacy feedback"),
        ),
        migrations.AlterField(
            model_name="homeworkattachment",
            name="assessment",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="homework_attachments",
                to="assessments.assessment",
                verbose_name="assessment",
            ),
        ),
        migrations.AlterField(
            model_name="homeworkattachment",
            name="student",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="homework_attachments",
                to=settings.AUTH_USER_MODEL,
                verbose_name="student",
            ),
        ),
        migrations.AlterField(
            model_name="homeworkattachment",
            name="attempt",
            field=models.ForeignKey(
                blank=True,
                help_text="Legacy per-file attempt created before aggregate submissions.",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="homework_attachments",
                to="assessments.assessmentattempt",
                verbose_name="legacy attempt",
            ),
        ),
        migrations.AlterField(
            model_name="homeworkattachment",
            name="reviewed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="reviewed_homework_attachments",
                to=settings.AUTH_USER_MODEL,
                verbose_name="reviewed by",
            ),
        ),
        migrations.AlterField(
            model_name="assessment",
            name="max_upload_files",
            field=models.PositiveIntegerField(
                blank=True,
                default=1,
                help_text="For homework/file assessments: maximum number of files a student may upload. Leave blank for no limit.",
                null=True,
                verbose_name="max uploaded files",
            ),
        ),
        migrations.CreateModel(
            name="HomeworkSubmission",
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
                        db_index=True, default=django.utils.timezone.now, editable=False
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("uploaded", "Uploaded"),
                            ("submitted", "Submitted"),
                            ("under_review", "Under review"),
                            ("accepted", "Accepted"),
                            ("needs_revision", "Needs revision"),
                            ("rejected", "Rejected"),
                            ("graded", "Graded"),
                            ("released", "Released"),
                        ],
                        db_index=True,
                        default="uploaded",
                        max_length=20,
                        verbose_name="status",
                    ),
                ),
                (
                    "submitted_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="submitted at"),
                ),
                (
                    "score",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=5,
                        null=True,
                        verbose_name="score",
                    ),
                ),
                ("feedback", models.TextField(blank=True, verbose_name="feedback")),
                (
                    "reviewed_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="reviewed at"),
                ),
                (
                    "legacy_status",
                    models.CharField(
                        choices=[
                            ("active", "Active"),
                            ("legacy_archived", "Legacy archived"),
                            ("legacy_review_required", "Legacy - review required"),
                        ],
                        db_index=True,
                        default="active",
                        help_text="Active submissions participate in workflows; legacy rows are preserved read-only.",
                        max_length=24,
                        verbose_name="legacy status",
                    ),
                ),
                ("legacy_note", models.TextField(blank=True, verbose_name="legacy note")),
                (
                    "assessment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="homework_submissions",
                        to="assessments.assessment",
                        verbose_name="assessment",
                    ),
                ),
                (
                    "student",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="homework_submissions",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="student",
                    ),
                ),
                (
                    "attempt",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="homework_submission",
                        to="assessments.assessmentattempt",
                        verbose_name="attempt",
                    ),
                ),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="reviewed_homework_submissions",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="reviewed by",
                    ),
                ),
            ],
            options={
                "verbose_name": "homework submission",
                "verbose_name_plural": "homework submissions",
                "ordering": ("-created_at",),
                "db_table": "assessments_homework_submission",
            },
        ),
        migrations.AddField(
            model_name="homeworkattachment",
            name="submission",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="attachments",
                to="assessments.homeworksubmission",
                verbose_name="homework submission",
            ),
        ),
        migrations.RunPython(
            create_aggregates_from_legacy_attachments,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddIndex(
            model_name="homeworksubmission",
            index=models.Index(
                fields=["assessment", "status"], name="assessments_assessm_c794f3_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="homeworksubmission",
            index=models.Index(fields=["student"], name="assessments_student_31104f_idx"),
        ),
        migrations.AddConstraint(
            model_name="homeworksubmission",
            constraint=models.UniqueConstraint(
                fields=("assessment", "student"),
                name="unique_homework_submission_per_student",
            ),
        ),
        migrations.AddConstraint(
            model_name="homeworksubmission",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("legacy_status", "active"),
                    ("score__isnull", True),
                    _connector="OR",
                ),
                name="check_homework_submission_legacy_ungraded",
            ),
        ),
        migrations.AddIndex(
            model_name="homeworkattachment",
            index=models.Index(
                fields=["assessment", "student"], name="assessments_assessm_f6ac39_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="homeworkattachment",
            index=models.Index(
                fields=["submission"], name="assessments_submiss_05d072_idx"
            ),
        ),
    ]
