from django.conf import settings
from django.db import migrations, models, transaction
import django.db.models.deletion
import django.utils.timezone


@transaction.atomic
def consolidate_diploma_sessions(apps, schema_editor):
    """Create one period per cohort/semester and one child day per date.

    Before this migration the application allowed several diploma session rows
    for the same day.  Records for the same enrollment on the same day are
    duplicate attendance events, so the earliest canonical record is kept and
    the merged record ids are retained in its metadata for auditability.
    """

    AttendancePeriod = apps.get_model("attendance", "AttendancePeriod")
    AttendanceSession = apps.get_model("attendance", "AttendanceSession")
    AttendanceRecord = apps.get_model("attendance", "AttendanceRecord")

    group_keys = (
        AttendanceSession.objects.filter(
            cohort_id__isnull=False,
            semester_id__isnull=False,
        )
        .values_list("cohort_id", "semester_id")
        .distinct()
    )

    for cohort_id, semester_id in group_keys:
        sessions = list(
            AttendanceSession.objects.filter(
                cohort_id=cohort_id,
                semester_id=semester_id,
            ).order_by("date", "id")
        )
        if not sessions:
            continue

        starts_at = min(session.open_at for session in sessions)
        ends_at = max(session.close_at for session in sessions)
        status_values = {session.status for session in sessions}
        if "open" in status_values:
            period_status = "open"
        elif status_values and status_values <= {"closed", "cancelled"}:
            period_status = "closed" if "closed" in status_values else "cancelled"
        else:
            period_status = "scheduled"

        first = sessions[0]
        period, created = AttendancePeriod.objects.get_or_create(
            cohort_id=cohort_id,
            semester_id=semester_id,
            defaults={
                "title": "Attendance period",
                "starts_at": starts_at,
                "ends_at": ends_at,
                "status": period_status,
                "opened_by_id": next(
                    (session.opened_by_id for session in sessions if session.opened_by_id),
                    None,
                ),
                "closed_by_id": next(
                    (session.closed_by_id for session in sessions if session.closed_by_id),
                    None,
                ),
                "created_by_id": first.created_by_id,
            },
        )
        if not created:
            changed = []
            if period.starts_at > starts_at:
                period.starts_at = starts_at
                changed.append("starts_at")
            if period.ends_at < ends_at:
                period.ends_at = ends_at
                changed.append("ends_at")
            if changed:
                period.save(update_fields=changed + ["updated_at"])

        for session_date in sorted({session.date for session in sessions}):
            same_day = [session for session in sessions if session.date == session_date]
            record_counts = {
                session.id: AttendanceRecord.objects.filter(session_id=session.id).count()
                for session in same_day
            }
            canonical = sorted(
                same_day,
                key=lambda item: (
                    item.status == "cancelled",
                    -record_counts[item.id],
                    item.id,
                ),
            )[0]
            duplicates = [session for session in same_day if session.id != canonical.id]

            for duplicate in duplicates:
                for record in list(
                    AttendanceRecord.objects.filter(session_id=duplicate.id).order_by("id")
                ):
                    lookup = {"diploma_enrollment_id": record.diploma_enrollment_id}
                    if record.course_enrollment_id is not None:
                        lookup = {"course_enrollment_id": record.course_enrollment_id}
                    existing = AttendanceRecord.objects.filter(
                        session_id=canonical.id,
                        **lookup,
                    ).first()
                    if existing:
                        metadata = dict(existing.metadata or {})
                        merged = list(metadata.get("merged_duplicate_record_ids", []))
                        merged.append(record.id)
                        metadata["merged_duplicate_record_ids"] = merged
                        existing.metadata = metadata
                        existing.save(update_fields=["metadata", "updated_at"])
                        record.delete()
                    else:
                        record.session_id = canonical.id
                        record.save(update_fields=["session_id", "updated_at"])
                duplicate.delete()

            canonical.period_id = period.id
            canonical.session_number = (
                AttendanceSession.objects.filter(
                    period_id=period.id,
                    date__lt=session_date,
                ).count()
                + 1
            )
            canonical.save(update_fields=["period", "session_number", "updated_at"])


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("attendance", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="AttendancePeriod",
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
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, db_index=True),
                ),
                (
                    "title",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=255,
                        verbose_name="title",
                    ),
                ),
                (
                    "starts_at",
                    models.DateTimeField(verbose_name="starts at"),
                ),
                (
                    "ends_at",
                    models.DateTimeField(verbose_name="ends at"),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("scheduled", "Scheduled"),
                            ("open", "Open"),
                            ("closed", "Closed"),
                            ("cancelled", "Cancelled"),
                        ],
                        db_index=True,
                        default="scheduled",
                        max_length=20,
                        verbose_name="status",
                    ),
                ),
                (
                    "closed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="closed_attendance_periods",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="closed by",
                    ),
                ),
                (
                    "cohort",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attendance_periods",
                        to="academics.cohort",
                        verbose_name="cohort",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="created_attendance_periods",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="created by",
                    ),
                ),
                (
                    "opened_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="opened_attendance_periods",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="opened by",
                    ),
                ),
                (
                    "semester",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attendance_periods",
                        to="academics.semester",
                        verbose_name="semester",
                    ),
                ),
            ],
            options={
                "verbose_name": "attendance period",
                "verbose_name_plural": "attendance periods",
            },
        ),
        migrations.AddField(
            model_name="attendancesession",
            name="period",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="sessions",
                to="attendance.attendanceperiod",
                verbose_name="attendance period",
            ),
        ),
        migrations.AddField(
            model_name="attendancesession",
            name="session_number",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                verbose_name="session number",
            ),
        ),
        migrations.RunPython(consolidate_diploma_sessions, migrations.RunPython.noop),
    ]
