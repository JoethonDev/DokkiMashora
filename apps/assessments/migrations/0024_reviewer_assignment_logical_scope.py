from collections import defaultdict

from django.db import migrations, models


def consolidate_reviewer_assignments(apps, schema_editor):
    ReviewerAssignment = apps.get_model("assessments", "ReviewerAssignment")
    ReviewerExamClaim = apps.get_model("assessments", "ReviewerExamClaim")

    grouped = defaultdict(list)
    for assignment in ReviewerAssignment.objects.filter(active=True).order_by("id"):
        key = (
            assignment.reviewer_id,
            assignment.cohort_id,
            assignment.scope,
            assignment.semester_id,
            assignment.assessment_id,
            assignment.cluster_id,
        )
        grouped[key].append(assignment)

    for rows in grouped.values():
        if len(rows) == 1 and rows[0].section_id is None:
            canonical = rows[0]
        else:
            claimed_ids = set(
                ReviewerExamClaim.objects.filter(
                    assignment_id__in=[row.id for row in rows],
                    status="in_progress",
                ).values_list("assignment_id", flat=True)
            )
            canonical = next(
                (row for row in rows if row.id in claimed_ids),
                next((row for row in rows if row.section_id is None), rows[0]),
            )

        canonical.section_id = None
        canonical.access_mode = "both"
        canonical.save(update_fields=["section", "access_mode", "updated_at"])

        canonical_has_claim = ReviewerExamClaim.objects.filter(
            assignment_id=canonical.id,
            status="in_progress",
        ).exists()
        for duplicate in rows:
            if duplicate.id == canonical.id:
                continue
            active_claims = list(
                ReviewerExamClaim.objects.filter(
                    assignment_id=duplicate.id,
                    status="in_progress",
                ).order_by("id")
            )
            for claim in active_claims:
                if not canonical_has_claim:
                    claim.assignment_id = canonical.id
                    claim.save(update_fields=["assignment", "updated_at"])
                    canonical_has_claim = True
            if not ReviewerExamClaim.objects.filter(
                assignment_id=duplicate.id,
                status="in_progress",
            ).exists():
                duplicate.active = False
                duplicate.save(update_fields=["active", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("assessments", "0023_assessmentattempt_assessments_student_662c1a_idx"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="reviewerassignment",
            name="uq_reviewer_assignment",
        ),
        migrations.AlterField(
            model_name="reviewerassignment",
            name="access_mode",
            field=models.CharField(
                choices=[
                    ("online", "Request online submissions"),
                    ("offline", "Enter offline grades"),
                    ("both", "Online and offline grading"),
                ],
                default="both",
                max_length=10,
                verbose_name="access mode",
            ),
        ),
        migrations.RunPython(consolidate_reviewer_assignments, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="reviewerassignment",
            constraint=models.UniqueConstraint(
                condition=models.Q(active=True, section__isnull=True),
                fields=("reviewer", "cohort", "scope", "semester", "assessment", "cluster"),
                name="uq_active_reviewer_assignment_scope",
                nulls_distinct=False,
            ),
        ),
    ]
