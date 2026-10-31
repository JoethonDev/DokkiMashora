from django.db import migrations


def preserve_existing_visibility(apps, schema_editor):
    Assessment = apps.get_model("assessments", "Assessment")
    AssessmentAttempt = apps.get_model("assessments", "AssessmentAttempt")
    database = schema_editor.connection.alias
    assessment_ids = (
        AssessmentAttempt.objects.using(database)
        .filter(visible_to_student=True)
        .values_list("assessment_id", flat=True)
        .distinct()
    )
    Assessment.objects.using(database).filter(pk__in=assessment_ids).update(
        grade_visibility="released",
    )


class Migration(migrations.Migration):

    dependencies = [
        ("assessments", "0029_remove_reviewerexamclaim_uq_active_reviewer_claim_attempt_section_and_more"),
    ]

    operations = [
        migrations.RunPython(preserve_existing_visibility, migrations.RunPython.noop),
    ]
