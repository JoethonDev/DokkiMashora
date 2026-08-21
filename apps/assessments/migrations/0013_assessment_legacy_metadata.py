"""P2-01: legacy assessment metadata, evidence-driven quarantine, and active-vocabulary constraints.

Adds ``legacy_status``/``legacy_note`` so deprecated or ambiguous assessment rows
(``final_exam``, ``additional_assessment``, dual-scope rows, non-one-attempt rows)
are preserved read-only instead of participating in active workflows. No attempt,
answer, score, question, submission, or outcome is deleted or rewritten. Deprecated
rows keep their original ``assessment_type`` value.
"""

from django.db import migrations, models


def classify_legacy_assessments(apps, schema_editor):
    Assessment = apps.get_model("assessments", "Assessment")
    REVIEW_REQUIRED = "legacy_review_required"
    DEPRECATED_TYPES = ("final_exam", "additional_assessment")
    LOCKED_COMBO = {
        "quiz": "course",
        "homework": "diploma",
        "semester_exam": "diploma",
    }

    def _set_legacy(assessment, note):
        assessment.legacy_status = REVIEW_REQUIRED
        assessment.legacy_note = note
        assessment.save(update_fields=["legacy_status", "legacy_note"])

    for assessment in Assessment.objects.all().iterator(chunk_size=500):
        if assessment.assessment_type in DEPRECATED_TYPES:
            _set_legacy(
                assessment,
                "Deprecated assessment type '%s' preserved as historical data; no durable "
                "evidence maps it to a locked active type (Quiz/Semester Exam/Homework). "
                "Review required." % assessment.assessment_type,
            )
            continue

        has_course_scope = assessment.course_version_id is not None
        has_any_diploma_scope = assessment.semester_id is not None or assessment.cohort_id is not None

        if has_course_scope and has_any_diploma_scope:
            _set_legacy(
                assessment,
                "Scope is inconsistent: both course_version and semester/cohort are set. "
                "The authoritative scope cannot be determined without guessing; preserved for review.",
            )
            continue

        if not has_course_scope and not has_any_diploma_scope:
            _set_legacy(
                assessment,
                "Assessment has no scope (no course_version and no semester/cohort); preserved for review.",
            )
            continue

        expected_scope = LOCKED_COMBO.get(assessment.assessment_type)
        actual_scope = "course" if has_course_scope else "diploma"
        if expected_scope != actual_scope:
            _set_legacy(
                assessment,
                "Deprecated type/scope combination: type '%s' with %s scope is outside the "
                "locked vocabulary (course=Quiz; diploma=Homework/Semester Exam). "
                "Preserved for review." % (assessment.assessment_type, actual_scope),
            )
            continue

        if assessment.max_attempts != 1:
            _set_legacy(
                assessment,
                "Active assessments allow exactly one attempt; this row has max_attempts=%s "
                "and is preserved for review." % assessment.max_attempts,
            )
            continue
        # Valid active row: no legacy flag change.


class Migration(migrations.Migration):
    dependencies = [
        ("assessments", "0012_semesterexamaccessoverride_used_count"),
    ]

    operations = [
        migrations.AddField(
            model_name="assessment",
            name="legacy_status",
            field=models.CharField(
                choices=[
                    ("active", "Active"),
                    ("legacy_archived", "Legacy archived"),
                    ("legacy_review_required", "Legacy - review required"),
                ],
                db_index=True,
                default="active",
                help_text="Active assessments participate in workflows; legacy rows are preserved read-only.",
                max_length=24,
                verbose_name="legacy status",
            ),
        ),
        migrations.AddField(
            model_name="assessment",
            name="legacy_note",
            field=models.TextField(
                blank=True,
                help_text="Reason a legacy assessment is archived or requires review.",
                verbose_name="legacy note",
            ),
        ),
        migrations.RunPython(classify_legacy_assessments, reverse_code=migrations.RunPython.noop),
    ]
