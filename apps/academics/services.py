"""Access policy and enrollment business logic for academics."""

import logging
import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Count, F, IntegerField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.academics import tasks as academic_tasks
from apps.academics.constants import (
    ENROLLMENT_STATUS_ACTIVE,
    ENROLLMENT_STATUS_COMPLETED,
    ENROLLMENT_STATUS_EXPIRED,
    SCOPE_COURSE_VERSION,
    SCOPE_DIPLOMA_COHORT,
    SCOPE_DIPLOMA_SEMESTER,
    SCOPE_SEMESTER_COHORT,
    SCOPE_WHOLE_DIPLOMA,
    VIOLATION_FORWARD_SKIP,
    VIOLATION_INVALID_POSITION,
    VIOLATION_INVALID_SEQUENCE,
    VIOLATION_NONE,
    VIOLATION_SPEED,
    WATCH_STATUS_COMPLETED,
    WATCH_STATUS_IN_PROGRESS,
    WATCH_STATUS_VIOLATED,
    YOUTUBE_METADATA_STATUS_PENDING,
    YOUTUBE_METADATA_STATUS_READY,
    YOUTUBE_METADATA_STATUS_UNAVAILABLE,
)
from apps.academics.models import (
    Cohort,
    CourseEnrollment,
    CourseVersion,
    DiplomaEnrollment,
    DiplomaLecture,
    DiplomaLectureWatchProgress,
    Lecture,
    LectureAudioProgress,
    LectureWatchProgress,
    ModeratorAssignment,
    ModeratorStudentAssignment,
    Semester,
    TeacherAssignment,
)
from apps.accounts.models import User
from apps.assessments.constants import ASSESSMENT_TYPE_QUIZ, LEGACY_STATUS_ACTIVE
from apps.assessments.models import Assessment, AssessmentAttempt
from apps.audit.constants import (
    ACTION_ACADEMIC_DELETED,
    ACTION_ENROLLMENT_CHANGE,
    SECTION_ACADEMICS,
)
from apps.audit.services import record_audit_event
from apps.progression.constants import (
    COMPONENT_TYPE_ATTENDANCE,
    COMPONENT_TYPE_HOMEWORK_ASSESSMENT,
    COMPONENT_TYPE_SEMESTER_EXAM,
    SEMESTER_PROGRESS_COMPLETED,
    SEMESTER_PROGRESS_IN_PROGRESS,
    SEMESTER_PROGRESS_NOT_STARTED,
    SEMESTER_PROGRESS_PASSED,
    SEMESTER_PROGRESS_PASSED_CONFIRMED,
    SEMESTER_PROGRESS_PROMOTED,
)
from apps.progression.models import (
    SemesterGradeComponent,
    StudentSemesterProgress,
)
from apps.storage.services import record_deferred_storage_deletion, storage_service

logger = logging.getLogger(__name__)


def activate_diploma_enrollment(enrollment: DiplomaEnrollment) -> DiplomaEnrollment:
    """Activate a pending diploma enrollment."""
    enrollment.status = ENROLLMENT_STATUS_ACTIVE
    enrollment.activated_at = timezone.now()
    if enrollment.cohort.end_date is not None:
        enrollment.expires_at = datetime.combine(
            enrollment.cohort.end_date,
            datetime.max.time(),
            tzinfo=timezone.get_current_timezone(),
        )
    enrollment.save(update_fields=["status", "activated_at", "expires_at", "updated_at"])
    return enrollment


def activate_course_enrollment(enrollment: CourseEnrollment) -> CourseEnrollment:
    """Activate a pending course enrollment and compute expiry from access duration."""
    enrollment.status = ENROLLMENT_STATUS_ACTIVE
    enrollment.activated_at = timezone.now()
    duration = enrollment.course_version.access_duration_days
    if duration is not None:
        enrollment.expires_at = enrollment.activated_at + timedelta(days=duration)
    enrollment.save(update_fields=["status", "activated_at", "expires_at", "updated_at"])
    return enrollment


def expire_stale_enrollments():
    """Mark any active enrollments whose expiry has passed as expired.

    Set-based conditional updates (P5-04): returns a bounded dict of affected
    counts without any student-sensitive identifiers. This is lifecycle
    normalization only — every active-access read independently enforces
    ``status=active`` AND non-expired at read time, so a delayed run never
    grants access.
    """
    now = timezone.now()
    diploma_count = DiplomaEnrollment.objects.filter(
        status=ENROLLMENT_STATUS_ACTIVE,
        expires_at__lte=now,
    ).update(status=ENROLLMENT_STATUS_EXPIRED)
    course_count = CourseEnrollment.objects.filter(
        status=ENROLLMENT_STATUS_ACTIVE,
        expires_at__lte=now,
    ).update(status=ENROLLMENT_STATUS_EXPIRED)
    return {
        "diploma_expired": diploma_count,
        "course_expired": course_count,
    }


def get_student_course_versions(student_profile):
    """Return the versions with an active, non-expired student enrollment."""
    now = timezone.now()
    return CourseVersion.objects.filter(
        enrollments__student=student_profile,
        enrollments__status=ENROLLMENT_STATUS_ACTIVE,
    ).filter(
        Q(enrollments__expires_at__isnull=True) | Q(enrollments__expires_at__gt=now),
    ).select_related("course")


def get_visible_lectures(student_profile, course_version):
    if not can_access_course(student_profile, course_version):
        return Lecture.objects.none()
    now = timezone.now()
    return (
        course_version.lectures.filter(
            Q(release_at__isnull=True) | Q(release_at__lte=now),
            is_published=True,
        )
        .order_by("order")
    )

# ---------------------------------------------------------------------------
# DUP-001 canonical diploma visibility (single + batch share one decision)
# ---------------------------------------------------------------------------

# TODO(phase5): remove 'passed' and 'completed' after migration
_DIPLOMA_COMPLETED_STATUSES = {
    SEMESTER_PROGRESS_COMPLETED,
    SEMESTER_PROGRESS_PASSED,
    SEMESTER_PROGRESS_PASSED_CONFIRMED,
    SEMESTER_PROGRESS_PROMOTED,
}
_DIPLOMA_IN_PROGRESS_STATUSES = {
    SEMESTER_PROGRESS_NOT_STARTED,
    SEMESTER_PROGRESS_IN_PROGRESS,
}


def _decide_visible_ids(
    semesters: list[Semester],
    progress_map: dict[int, StudentSemesterProgress],
) -> list[int]:
    """Canonical per-enrollment visibility decision (DUP-001).

    Completed semesters remain visible, and the first non-completed semester
    is the only current semester shown. Semester completion/progression state
    is authoritative; legacy academic-year metadata and promotion rows never
    create or block access. Single and batch paths share this exact logic.
    """
    visible_ids: list[int] = []
    for semester in semesters:
        item = progress_map.get(semester.id)
        if item and item.status in _DIPLOMA_COMPLETED_STATUSES:
            visible_ids.append(semester.id)
            continue
        visible_ids.append(semester.id)
        break
    return visible_ids


def get_student_diploma_access(enrollment):
    """Return only the completed and currently unlocked diploma semesters.

    Uses the canonical _decide_visible_ids decision; blocker metadata is
    derived from the same inputs so single and batch remain equivalent.
    """
    semesters = list(
        enrollment.cohort.diploma.semesters.filter(is_active=True).order_by("order")
    )
    entry_index = next(
        (index for index, semester in enumerate(semesters) if semester.id == enrollment.entry_semester_id),
        0,
    )
    available_semesters = semesters[entry_index:]
    progress = {
        item.semester_id: item
        for item in StudentSemesterProgress.objects.filter(diploma_enrollment=enrollment)
    }
    visible_ids = _decide_visible_ids(available_semesters, progress)
    semester_by_id = {s.id: s for s in semesters}
    visible = [semester_by_id[sid] for sid in visible_ids]

    # Derive current / completed / hidden mirroring original semantics
    current = None
    if visible:
        last = visible[-1]
        last_item = progress.get(last.id)
        is_last_completed = bool(last_item and last_item.status in _DIPLOMA_COMPLETED_STATUSES)
        if not is_last_completed:
            current = last

    blockers: list[str] = []
    if current is not None:
        item = progress.get(current.id)
        if item and item.status not in _DIPLOMA_IN_PROGRESS_STATUSES:
            blockers.append(item.status)

    return {
        "completed_semesters": visible[:-1] if current else visible,
        "current_semester": current,
        "visible_semesters": visible,
        "hidden_semesters": semesters[:entry_index] + available_semesters[len(visible):],
        "blockers": blockers,
    }


def get_visible_semester_ids_by_enrollment(enrollments) -> dict[int, set[int]]:
    """Batch diploma semester visibility checks for dashboard and assessment lists.

    Preloads the same inputs as the single path and delegates to the canonical
    _decide_visible_ids, so per-enrollment semantics are identical while the
    query count stays constant.
    """
    enrollments = list(enrollments)
    if not enrollments:
        return {}

    enrollment_ids = [enrollment.id for enrollment in enrollments]
    semesters_by_diploma: dict[int, list[Semester]] = {}
    for semester in Semester.objects.filter(
        diploma_id__in={e.cohort.diploma_id for e in enrollments}, is_active=True,
    ).order_by("order"):
        semesters_by_diploma.setdefault(semester.diploma_id, []).append(semester)

    progress_by_enrollment: dict[int, dict[int, StudentSemesterProgress]] = {}
    for prog in StudentSemesterProgress.objects.filter(diploma_enrollment_id__in=enrollment_ids):
        progress_by_enrollment.setdefault(prog.diploma_enrollment_id, {})[prog.semester_id] = prog

    visible_by_enrollment: dict[int, set[int]] = {}
    for enrollment in enrollments:
        semesters = semesters_by_diploma.get(enrollment.cohort.diploma_id, [])
        entry_index = next(
            (index for index, semester in enumerate(semesters) if semester.id == enrollment.entry_semester_id),
            0,
        )
        available_semesters = semesters[entry_index:]
        progress = progress_by_enrollment.get(enrollment.id, {})
        visible_ids = _decide_visible_ids(available_semesters, progress)
        visible_by_enrollment[enrollment.id] = set(visible_ids)
    return visible_by_enrollment


def get_visible_semesters_by_enrollment(enrollments) -> dict[int, list[Semester]]:
    """Return canonical visible Semester objects for each enrollment in batches."""
    enrollments = list(enrollments)
    if not enrollments:
        return {}

    visible_ids_by_enrollment = get_visible_semester_ids_by_enrollment(enrollments)
    semesters_by_id = {
        semester.id: semester
        for semester in Semester.objects.filter(
            diploma_id__in={enrollment.cohort.diploma_id for enrollment in enrollments},
            is_active=True,
        ).order_by("diploma_id", "order")
    }
    visible_semesters_by_enrollment = {}
    for enrollment in enrollments:
        visible_semesters = [
            semesters_by_id[semester_id]
            for semester_id in visible_ids_by_enrollment.get(enrollment.id, set())
            if semester_id in semesters_by_id
        ]
        visible_semesters_by_enrollment[enrollment.id] = sorted(
            visible_semesters,
            key=lambda semester: (semester.order, semester.id),
        )
    return visible_semesters_by_enrollment


def can_access_course(student_profile, course_version) -> bool:
    """Check whether the student has an active, non-expired enrollment."""
    enrollment = _get_course_enrollment(student_profile, course_version)
    if enrollment is not None:
        if enrollment.status != ENROLLMENT_STATUS_ACTIVE:
            return False
        if enrollment.is_access_expired:
            return False
        return True
    return False


# --- Course sequential completion (P5-01) -------------------------------------
#
# One canonical course-only predicate: a lecture is completed when it has no
# trackable required media, or its video is completed, or its audio is
# completed. When both video and audio exist, EITHER completion is sufficient.
# Completion never trusts client-reported duration or browser events; it is
# derived only from server-validated contiguous seconds vs persisted duration.


def _lecture_has_video_source(lecture) -> bool:
    return bool(lecture.youtube_url or lecture.youtube_video_id)


def _lecture_has_audio_source(lecture) -> bool:
    return bool(lecture.audio_storage_key or lecture.audio_url)


def _lecture_has_video(lecture) -> bool:
    return bool(lecture.youtube_video_id and lecture.youtube_duration_seconds)


def _lecture_has_audio(lecture) -> bool:
    return bool(lecture.audio_hls_playlist_key and lecture.audio_duration_seconds)


def _lecture_has_trackable_media(lecture) -> bool:
    return _lecture_has_video_source(lecture) or _lecture_has_audio_source(lecture)


def _completed_video_ids(student, enrollment, lecture_qs) -> set[int]:
    return set(
        LectureWatchProgress.objects.filter(
            lecture__in=lecture_qs,
            student=student,
            course_enrollment=enrollment,
            status=WATCH_STATUS_COMPLETED,
        ).values_list("lecture_id", flat=True)
    )


def _completed_audio_ids(student, enrollment, lecture_qs) -> set[int]:
    return set(
        LectureAudioProgress.objects.filter(
            lecture__in=lecture_qs,
            student=student,
            course_enrollment=enrollment,
            status=WATCH_STATUS_COMPLETED,
        ).values_list("lecture_id", flat=True)
    )


def _course_lecture_completion_map(student_profile, enrollment, lecture_qs) -> dict[int, bool]:
    """Set-based completion map for a released/published lecture queryset."""
    video_completed = _completed_video_ids(student_profile, enrollment, lecture_qs)
    audio_completed = _completed_audio_ids(student_profile, enrollment, lecture_qs)
    result: dict[int, bool] = {}
    for lecture in lecture_qs:
        if not _lecture_has_trackable_media(lecture):
            result[lecture.id] = True
            continue
        if lecture.id in video_completed:
            result[lecture.id] = True
            continue
        if _lecture_has_audio(lecture) and lecture.id in audio_completed:
            result[lecture.id] = True
            continue
        result[lecture.id] = False
    return result


def _course_unlock_map(student_profile, enrollment, lecture_qs) -> dict[int, bool]:
    """Sequential unlock: each released/published lecture is open only when all
    previous released/published lectures are completed when video tracking is
    enabled for the course."""
    ordered = list(lecture_qs.order_by("order"))
    if not enrollment.course_version.course.video_tracking_enabled:
        return {lecture.id: True for lecture in ordered}
    completion = _course_lecture_completion_map(student_profile, enrollment, ordered)
    unlocked: dict[int, bool] = {}
    blocked = False
    for lecture in ordered:
        unlocked[lecture.id] = not blocked
        if not completion.get(lecture.id, False):
            blocked = True
    return unlocked


def can_access_lecture(student_profile, lecture) -> bool:
    """Check whether the student can view a specific course lecture.

    The student must have active course access and the lecture must be
    released. When course video tracking is enabled, every previous
    released/published lecture must also be completed. Diploma lectures are
    never locked by watch/listen progress.
    """
    if not can_access_course(student_profile, lecture.course_version):
        return False
    if not lecture.is_released:
        return False
    enrollment = _get_course_enrollment(student_profile, lecture.course_version)
    if enrollment is None:
        return False
    if not lecture.course_version.course.video_tracking_enabled:
        return True
    previous = (
        lecture.course_version.lectures.filter(
            Q(release_at__isnull=True) | Q(release_at__lte=timezone.now()),
            is_published=True,
            order__lt=lecture.order,
        )
    )
    if not previous.exists():
        return True
    completion = _course_lecture_completion_map(student_profile, enrollment, previous)
    return all(completion.get(item.id, False) for item in previous)


def _get_course_enrollment(student_profile, course_version):
    return CourseEnrollment.objects.filter(
        student=student_profile,
        course_version=course_version,
    ).first()


def enroll_student_in_course(student_profile, course_version) -> CourseEnrollment:
    """Create or get an active course enrollment for a student."""
    enrollment, _created = CourseEnrollment.objects.get_or_create(
        student=student_profile,
        course_version=course_version,
        defaults={"status": ENROLLMENT_STATUS_ACTIVE},
    )
    if enrollment.status != ENROLLMENT_STATUS_ACTIVE:
        enrollment.status = ENROLLMENT_STATUS_ACTIVE
        enrollment.save(update_fields=["status"])
    return enrollment


def enroll_student_in_diploma(student_profile, cohort) -> DiplomaEnrollment:
    """Create or get an active diploma enrollment for a student."""
    enrollment, _created = DiplomaEnrollment.objects.get_or_create(
        student=student_profile,
        cohort=cohort,
        defaults={"status": ENROLLMENT_STATUS_ACTIVE},
    )
    if enrollment.status != ENROLLMENT_STATUS_ACTIVE:
        enrollment.status = ENROLLMENT_STATUS_ACTIVE
        enrollment.save(update_fields=["status"])
    return enrollment


def assign_teacher_to_cohort(teacher, cohort, actor) -> TeacherAssignment:
    """Create or reactivate a teacher assignment for a cohort (delegates to new model)."""
    return assign_teacher(teacher, SCOPE_DIPLOMA_COHORT, actor, cohort_id=cohort.id)


def assign_moderator_to_students(
    moderator,
    students,
    cohort,
    actor,
) -> list[ModeratorStudentAssignment]:
    """Merge-add moderator assignments for the given students in a cohort."""
    assignments = []
    for student in students:
        assignment, created = ModeratorStudentAssignment.objects.get_or_create(
            moderator=moderator,
            student=student,
            cohort=cohort,
            active=True,
            defaults={"assigned_by": actor},
        )
        if not created and assignment.assigned_by_id != actor.id:
            assignment.assigned_by = actor
            assignment.save(update_fields=["assigned_by", "updated_at"])
        assignments.append(assignment)
    return assignments


def revoke_moderator_student_assignment(
    assignment: ModeratorStudentAssignment,
    actor,
    reason: str = "",
) -> ModeratorStudentAssignment:
    """Revoke a moderator assignment without deleting history."""
    assignment.active = False
    assignment.revoked_by = actor
    assignment.revoked_at = timezone.now()
    assignment.revoke_reason = reason
    assignment.save(update_fields=["active", "revoked_by", "revoked_at", "revoke_reason", "updated_at"])
    return assignment


# --- TeacherAssignment (generalized) services ---------------------------------

def assign_teacher(teacher, scope_type, actor, **scope_ids) -> TeacherAssignment:
    """Create a teacher assignment for the given scope.

    Keyword scope_ids must match the scope_type:
       whole_diploma     -> diploma_id
      diploma_semester  -> semester_id
      diploma_cohort    -> cohort_id
      semester_cohort   -> semester_id + cohort_id
    """
    defaults = {
        "teacher": teacher,
        "scope_type": scope_type,
        "assigned_by": actor,
        "active": True,
        "course_id": scope_ids.get("course_id"),
        "diploma_id": scope_ids.get("diploma_id"),
        "semester_id": scope_ids.get("semester_id"),
        "cohort_id": scope_ids.get("cohort_id"),
    }
    return TeacherAssignment.objects.create(**defaults)


def revoke_teacher_assignment(assignment: TeacherAssignment, actor, reason: str = "") -> TeacherAssignment:
    """Revoke a teacher assignment without deleting history."""
    assignment.active = False
    assignment.revoked_by = actor
    assignment.revoked_at = timezone.now()
    assignment.revoke_reason = reason
    assignment.save(update_fields=["active", "revoked_by", "revoked_at", "revoke_reason", "updated_at"])
    return assignment


def teacher_course_versions(user) -> list[CourseVersion]:
    """Return all CourseVersions a teacher has access to via their course-version assignments."""
    if not user or not user.is_authenticated:
        return []
    return list(CourseVersion.objects.filter(
        teacher_assignments__teacher=user,
        teacher_assignments__active=True,
        teacher_assignments__scope_type=SCOPE_COURSE_VERSION,
    ).distinct())


def teacher_students(user) -> list:
    """Return all Users who are students in the teacher's scope."""
    if not user or not user.is_authenticated:
        return []
    qs = TeacherAssignment.objects.filter(teacher=user, active=True)
    student_ids = set()
    for a in qs:
        if a.scope_type == SCOPE_WHOLE_DIPLOMA:
            ids = DiplomaEnrollment.objects.filter(
                cohort__diploma=a.diploma
            ).values_list("student__user_id", flat=True)
            student_ids.update(ids)
        elif a.scope_type == SCOPE_DIPLOMA_SEMESTER:
            ids = DiplomaEnrollment.objects.filter(
                cohort__diploma__semesters=a.semester
            ).values_list("student__user_id", flat=True)
            student_ids.update(ids)
        elif a.scope_type == SCOPE_DIPLOMA_COHORT:
            ids = DiplomaEnrollment.objects.filter(
                cohort=a.cohort
            ).values_list("student__user_id", flat=True)
            student_ids.update(ids)
        elif a.scope_type == SCOPE_SEMESTER_COHORT:
            ids = DiplomaEnrollment.objects.filter(
                cohort=a.cohort
            ).values_list("student__user_id", flat=True)
            student_ids.update(ids)
    return User.objects.filter(id__in=list(student_ids)) if student_ids else User.objects.none()


# --- Diploma course/cohort management services -------------------------------


def create_cohort_with_defaults(*, diploma, form_data, actor):
    """Create a cohort without creating CourseVersion rows."""
    with transaction.atomic():
        cohort = Cohort.objects.create(
            diploma=diploma,
            name=form_data["name"],
            start_date=form_data["start_date"],
            end_date=form_data.get("end_date"),
            is_active=form_data.get("is_active", True),
            created_by=actor,
        )

        semesters = diploma.semesters.filter(is_active=True).order_by("order")

        defaults_by_type = {
            COMPONENT_TYPE_SEMESTER_EXAM: {
                "name": _("Semester Exam"),
                "weight_percent": 40,
                "required_min_percent": 50,
                "required_to_pass": True,
                "order": 1,
            },
            COMPONENT_TYPE_ATTENDANCE: {
                "name": _("Attendance"),
                "weight_percent": 10,
                "required_min_percent": 80,
                "required_to_pass": True,
                "order": 2,
            },
            COMPONENT_TYPE_HOMEWORK_ASSESSMENT: {
                "name": _("Homework/Assessment"),
                "weight_percent": 50,
                "required_min_percent": 50,
                "required_to_pass": True,
                "order": 3,
            },
        }

        components = []
        for semester in semesters:
            for ctype, defaults in defaults_by_type.items():
                components.append(SemesterGradeComponent(
                    cohort=cohort,
                    semester=semester,
                    component_type=ctype,
                    name=defaults["name"],
                    weight_percent=defaults["weight_percent"],
                    required_min_percent=defaults["required_min_percent"],
                    required_to_pass=defaults["required_to_pass"],
                    order=defaults["order"],
                    active=True,
                ))
        SemesterGradeComponent.objects.bulk_create(components)

    return cohort


def create_cohort(*, diploma, form_data, actor):
    """Create a cohort. Delegates to create_cohort_with_defaults; no CourseVersion rows created."""
    return create_cohort_with_defaults(diploma=diploma, form_data=form_data, actor=actor)


# --- Deletion services -------------------------------------------------------

# apps/audit/constants.py has no per-entity delete actions and no dedicated
# assignment-removal action. Every hard deletion therefore uses
# ACTION_ACADEMIC_DELETED and carries the concrete entity in metadata.


def _remove_scope_assignment_rows(queryset, *, actor, assignment_type):
    """Delete assignment rows that would raise ProtectedError and audit each removal.

    The academic deletion rules require blocking PROTECT scope rows to be deleted
    explicitly (with an audit event each) so the target delete never 500s; declared
    SET_NULL relations are left to the database.
    """
    removed = 0
    for assignment in queryset:
        before = {"active": assignment.active, "assigned_by_id": assignment.assigned_by_id}
        for field in (
            "teacher_id",
            "moderator_id",
            "course_id",
            "course_version_id",
            "diploma_id",
            "semester_id",
            "cohort_id",
            "cluster_id",
        ):
            if hasattr(assignment, field):
                before[field] = getattr(assignment, field)
        scope_label = (
            assignment.get_scope_type_display()
            if hasattr(assignment, "get_scope_type_display")
            else ""
        )
        base_label = str(assignment._meta.verbose_name)
        entity_label = f"{base_label} - {scope_label}" if scope_label else base_label
        record_audit_event(
            actor=actor,
            action=ACTION_ACADEMIC_DELETED,
            target_type=assignment_type,
            target_id=str(assignment.id),
            section=SECTION_ACADEMICS,
            target_label=entity_label,
            metadata={
                "entity_type": assignment_type,
                "entity_id": str(assignment.id),
                "entity_label": entity_label,
                "before": before,
            },
        )
        assignment.delete()
        removed += 1
    return removed


def _record_academic_delete(*, actor, target_type, target, before, target_label=""):
    """Append the single entity-agnostic deletion event for a target record."""
    label = target_label or str(
        before.get("title") or before.get("name") or before.get("label") or ""
    )
    record_audit_event(
        actor=actor,
        action=ACTION_ACADEMIC_DELETED,
        target_type=target_type,
        target_id=str(target.pk),
        section=SECTION_ACADEMICS,
        target_label=label,
        metadata={
            "entity_type": target_type,
            "entity_id": str(target.pk),
            "entity_label": label,
            "before": before,
        },
    )


def _audio_prefix_for_lecture(lecture_id: int, *, diploma: bool) -> str:
    return f"diploma-lectures/{lecture_id}/audio" if diploma else f"lectures/{lecture_id}/audio"


def _schedule_audio_cleanup(lecture_keys, prefixes) -> None:
    """Delete physical storage objects only after the DB transaction commits.

    Database history and physical-object cleanup stay separate (S2-33): the
    delete services remove DB rows inside the atomic block, and the storage
    cleanup runs post-commit. Failures are recorded as deferred deletions so a
    reconciliation command can retry them; cleanup is never fatal.
    """
    def _cleanup():
        for key in lecture_keys:
            if not key:
                continue
            try:
                ok = storage_service.delete_file(key)
            except Exception as exc:  # noqa: BLE001 - cleanup is best-effort post-commit
                logger.exception("Storage cleanup failed for key %s", key)
                record_deferred_storage_deletion(key=key, error=str(exc))
                continue
            if not ok:
                logger.warning("Storage cleanup deferred for key %s", key)
                record_deferred_storage_deletion(key=key, error="delete failed")
        for prefix in prefixes:
            try:
                ok = storage_service.delete_prefix(prefix)
            except Exception as exc:  # noqa: BLE001 - cleanup is best-effort post-commit
                logger.exception("Storage cleanup failed for prefix %s", prefix)
                record_deferred_storage_deletion(prefix=prefix, error=str(exc))
                continue
            if not ok:
                logger.warning("Storage cleanup deferred for prefix %s", prefix)
                record_deferred_storage_deletion(prefix=prefix, error="delete failed")

    transaction.on_commit(_cleanup)


def _assert_no_assessment_history(assessment_qs):
    """Prevent academic cascades from erasing assessment history."""
    if assessment_qs.filter(attempts__isnull=False).exists() or assessment_qs.filter(
        homework_submissions__isnull=False,
    ).exists():
        raise ValueError(
            _(
                "This academic record cannot be deleted because it contains "
                "student assessment or submission history. Deactivate it instead."
            )
        )


def delete_diploma_record(*, diploma, actor):
    """Hard-delete a diploma and its owned delivery content, preserving immutable history.

    Blocks with a ValueError when entrance exams still reference the diploma: their
    PROTECT relation cannot be detached, and deleting them would cascade student
    attempt history. Application, payment, issued-certificate, promotion-decision,
    and audit rows are preserved (SET_NULL) and never deleted.
    """
    with transaction.atomic():
        if diploma.entrance_exams.exists():
            raise ValueError(
                _("This diploma cannot be deleted because it still has entrance exams. "
                "Delete or archive its entrance exams first.")
            )
        _assert_no_assessment_history(
            Assessment.objects.filter(cohort__diploma=diploma)
            | Assessment.objects.filter(semester__diploma=diploma)
        )
        _remove_scope_assignment_rows(
            TeacherAssignment.objects.filter(diploma=diploma),
            actor=actor,
            assignment_type="teacher_assignment",
        )
        _remove_scope_assignment_rows(
            ModeratorAssignment.objects.filter(cluster__cohort__diploma=diploma),
            actor=actor,
            assignment_type="moderator_assignment",
        )
        lecture_rows = list(
            DiplomaLecture.objects.filter(cohort__diploma=diploma)
            .values_list("id", "audio_storage_key")
        )
        lecture_keys = [row[1] for row in lecture_rows]
        prefixes = [_audio_prefix_for_lecture(row[0], diploma=True) for row in lecture_rows]
        _record_academic_delete(
            actor=actor,
            target_type="diploma",
            target=diploma,
            before={
                "title": diploma.title,
                "is_active": diploma.is_active,
                "total_semesters": diploma.total_semesters,
                "attendance_enabled": diploma.attendance_enabled,
                "video_tracking_enabled": diploma.video_tracking_enabled,
                "video_completion_percent": diploma.video_completion_percent,
            },
        )
        diploma.delete()
        _schedule_audio_cleanup(lecture_keys, prefixes)


def delete_semester_record(*, semester, actor):
    """Hard-delete a semester and its owned delivery content, preserving immutable history.

    Diploma-lecture audio objects are removed post-commit (S2-33); DB history
    stays separate from storage cleanup.
    """
    with transaction.atomic():
        _assert_no_assessment_history(Assessment.objects.filter(semester=semester))
        lecture_rows = list(semester.diploma_lectures.values_list("id", "audio_storage_key"))
        lecture_keys = [row[1] for row in lecture_rows]
        prefixes = [_audio_prefix_for_lecture(row[0], diploma=True) for row in lecture_rows]
        _record_academic_delete(
            actor=actor,
            target_type="semester",
            target=semester,
            before={
                "diploma_id": semester.diploma_id,
                "order": semester.order,
                "title": semester.title,
                "is_active": semester.is_active,
            },
        )
        semester.delete()
        _schedule_audio_cleanup(lecture_keys, prefixes)


def delete_course_record(*, course, actor):
    """Hard-delete a course with its versions and content, preserving immutable history.

    Replaces the former delete_diploma_course: course deletion also removes the
    course-version teacher/moderator scope rows that would raise ProtectedError.
    """
    with transaction.atomic():
        _assert_no_assessment_history(Assessment.objects.filter(course_version__course=course))
        _remove_scope_assignment_rows(
            TeacherAssignment.objects.filter(course_version__course=course),
            actor=actor,
            assignment_type="teacher_assignment",
        )
        _remove_scope_assignment_rows(
            ModeratorAssignment.objects.filter(course_version__course=course),
            actor=actor,
            assignment_type="moderator_assignment",
        )
        lecture_rows = list(
            Lecture.objects.filter(course_version__course=course)
            .values_list("id", "audio_storage_key")
        )
        lecture_keys = [row[1] for row in lecture_rows]
        prefixes = [_audio_prefix_for_lecture(row[0], diploma=False) for row in lecture_rows]
        _record_academic_delete(
            actor=actor,
            target_type="course",
            target=course,
            before={
                "title": course.title,
                "is_active": course.is_active,
                "attendance_enabled": course.attendance_enabled,
                "video_tracking_enabled": course.video_tracking_enabled,
                "video_completion_percent": course.video_completion_percent,
            },
        )
        course.delete()
        _schedule_audio_cleanup(lecture_keys, prefixes)


def delete_course_version_record(*, version, actor):
    """Hard-delete a course version with its content, preserving immutable history.

    Physical audio objects are removed post-commit (S2-33); DB history stays
    separate from storage cleanup.
    """
    with transaction.atomic():
        _assert_no_assessment_history(Assessment.objects.filter(course_version=version))
        _remove_scope_assignment_rows(
            TeacherAssignment.objects.filter(course_version=version),
            actor=actor,
            assignment_type="teacher_assignment",
        )
        _remove_scope_assignment_rows(
            ModeratorAssignment.objects.filter(course_version=version),
            actor=actor,
            assignment_type="moderator_assignment",
        )
        lecture_rows = list(version.lectures.values_list("id", "audio_storage_key"))
        lecture_keys = [row[1] for row in lecture_rows]
        prefixes = [_audio_prefix_for_lecture(row[0], diploma=False) for row in lecture_rows]
        _record_academic_delete(
            actor=actor,
            target_type="course_version",
            target=version,
            before={
                "course_id": version.course_id,
                "label": version.label,
                "access_duration_days": version.access_duration_days,
                "is_active": version.is_active,
            },
        )
        version.delete()
        _schedule_audio_cleanup(lecture_keys, prefixes)


def delete_lecture_record(*, lecture, actor):
    """Hard-delete a course lecture and its watch progress, preserving immutable history.

    Physical audio objects are removed post-commit (S2-33).
    """
    with transaction.atomic():
        lecture_keys = [lecture.audio_storage_key]
        prefix = _audio_prefix_for_lecture(lecture.id, diploma=False)
        _record_academic_delete(
            actor=actor,
            target_type="lecture",
            target=lecture,
            before={
                "course_version_id": lecture.course_version_id,
                "order": lecture.order,
                "title": lecture.title,
                "is_published": lecture.is_published,
                "release_at": lecture.release_at.isoformat() if lecture.release_at else "",
                "youtube_video_id": lecture.youtube_video_id,
                "audio_processing_status": lecture.audio_processing_status,
                "has_audio": bool(
                    lecture.audio_url
                    or lecture.audio_storage_key
                    or lecture.audio_hls_playlist_key
                ),
            },
        )
        lecture.delete()
        _schedule_audio_cleanup(lecture_keys, [prefix])


def delete_diploma_lecture_record(*, lecture, actor):
    """Hard-delete a diploma lecture and its watch progress, preserving immutable history.

    Physical audio objects are removed post-commit (S2-33).
    """
    with transaction.atomic():
        lecture_keys = [lecture.audio_storage_key]
        prefix = _audio_prefix_for_lecture(lecture.id, diploma=True)
        _record_academic_delete(
            actor=actor,
            target_type="diploma_lecture",
            target=lecture,
            before={
                "cohort_id": lecture.cohort_id,
                "semester_id": lecture.semester_id,
                "order": lecture.order,
                "title": lecture.title,
                "is_published": lecture.is_published,
                "release_at": lecture.release_at.isoformat() if lecture.release_at else "",
                "youtube_video_id": lecture.youtube_video_id,
                "audio_processing_status": lecture.audio_processing_status,
                "has_audio": bool(
                    lecture.audio_url
                    or lecture.audio_storage_key
                    or lecture.audio_hls_playlist_key
                ),
            },
        )
        lecture.delete()
        _schedule_audio_cleanup(lecture_keys, [prefix])


def delete_cohort_record(*, cohort, actor):
    """Hard-delete a cohort's delivery and student relations, preserving immutable history.

    Replaces delete_cohort_delivery: cluster-scoped moderator assignment rows are
    removed explicitly (cluster is PROTECT) with an audit event each. Enrollments,
    progression, clusters, and assignments cascade as declared; application, payment,
    issued-certificate, promotion-decision, and audit rows are preserved (SET_NULL).
    Diploma-lecture audio objects are removed post-commit (S2-33).
    """
    with transaction.atomic():
        _assert_no_assessment_history(Assessment.objects.filter(cohort=cohort))
        _remove_scope_assignment_rows(
            ModeratorAssignment.objects.filter(cluster__cohort=cohort),
            actor=actor,
            assignment_type="moderator_assignment",
        )
        lecture_rows = list(cohort.lectures.values_list("id", "audio_storage_key"))
        lecture_keys = [row[1] for row in lecture_rows]
        prefixes = [_audio_prefix_for_lecture(row[0], diploma=True) for row in lecture_rows]
        _record_academic_delete(
            actor=actor,
            target_type="cohort",
            target=cohort,
            before={
                "diploma_id": cohort.diploma_id,
                "name": cohort.name,
                "start_date": cohort.start_date.isoformat(),
                "end_date": cohort.end_date.isoformat() if cohort.end_date else "",
                "is_active": cohort.is_active,
            },
        )
        cohort.delete()
        _schedule_audio_cleanup(lecture_keys, prefixes)


# --- YouTube metadata --------------------------------------------------------

_YT_VIDEO_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?.*?v=|embed/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)


def extract_youtube_video_id(url: str) -> str:
    """Extract the 11-character video ID from a YouTube URL, or return empty string."""
    m = _YT_VIDEO_ID_RE.search(url)
    return m.group(1) if m else ""


def canonicalize_youtube_id(lecture) -> None:
    """Persist the canonical 11-character video ID derived from ``youtube_url``.

    Remote metadata is never fetched from a student request path (S2-03); the
    duration/embeddable state is fetched by the bounded Celery task
    ``refresh_youtube_metadata_task`` only when ``settings.YOUTUBE_API_KEY``
    is configured.
    """
    video_id = extract_youtube_video_id(lecture.youtube_url) if lecture.youtube_url else ""
    changed_fields = []

    if video_id != lecture.youtube_video_id:
        lecture.youtube_video_id = video_id
        lecture.youtube_duration_seconds = None
        lecture.youtube_metadata_fetched_at = None
        lecture.youtube_metadata_request_id = None
        changed_fields.extend(
            [
                "youtube_video_id",
                "youtube_duration_seconds",
                "youtube_metadata_fetched_at",
                "youtube_metadata_request_id",
            ]
        )

    if video_id and lecture.youtube_duration_seconds and lecture.youtube_duration_seconds > 0:
        status = YOUTUBE_METADATA_STATUS_READY
    elif video_id and lecture.youtube_metadata_fetched_at:
        status = YOUTUBE_METADATA_STATUS_UNAVAILABLE
    elif video_id:
        status = YOUTUBE_METADATA_STATUS_PENDING
    elif lecture.youtube_url:
        status = YOUTUBE_METADATA_STATUS_UNAVAILABLE
    else:
        status = ""

    if lecture.youtube_metadata_status != status:
        lecture.youtube_metadata_status = status
        changed_fields.append("youtube_metadata_status")
    if not video_id and lecture.youtube_metadata_request_id is not None:
        lecture.youtube_metadata_request_id = None
        changed_fields.append("youtube_metadata_request_id")

    if changed_fields:
        lecture.save(update_fields=[*dict.fromkeys(changed_fields), "updated_at"])


def enqueue_youtube_metadata_refresh(
    lecture,
    *,
    diploma: bool = False,
    force: bool = False,
) -> str:
    """Queue a metadata fetch and bind its result to this video/request generation.

    ``force`` is reserved for an explicit staff retry. A failed broker enqueue
    becomes a durable unavailable state. Tasks are published only after the
    lecture write commits, and a stale request cannot update a changed video
    or a newer retry for the same video.
    """
    canonicalize_youtube_id(lecture)
    if not lecture.youtube_video_id:
        return "invalid"
    if (
        not force
        and lecture.youtube_metadata_status == YOUTUBE_METADATA_STATUS_READY
        and lecture.youtube_duration_seconds
        and lecture.youtube_duration_seconds > 0
    ):
        return "ready"
    if (
        not force
        and lecture.youtube_metadata_status == YOUTUBE_METADATA_STATUS_PENDING
        and lecture.youtube_metadata_request_id is not None
    ):
        return "pending"

    request_id = uuid.uuid4()
    model = lecture.__class__
    now = timezone.now()
    queued = model.objects.filter(
        pk=lecture.pk,
        youtube_video_id=lecture.youtube_video_id,
    ).update(
        youtube_metadata_status=YOUTUBE_METADATA_STATUS_PENDING,
        youtube_metadata_request_id=request_id,
        youtube_metadata_fetched_at=None,
        updated_at=now,
    )
    if not queued:
        return "changed"

    lecture.youtube_metadata_status = YOUTUBE_METADATA_STATUS_PENDING
    lecture.youtube_metadata_request_id = request_id
    lecture.youtube_metadata_fetched_at = None
    video_id = lecture.youtube_video_id

    def publish_request():
        try:
            academic_tasks.refresh_youtube_metadata_task.delay(
                lecture.pk,
                diploma=diploma,
                expected_video_id=video_id,
                request_id=str(request_id),
            )
        except Exception as exc:  # noqa: BLE001 - broker down; persist the unavailable state
            logger.error("Could not enqueue YouTube metadata fetch for lecture %s: %s", lecture.pk, exc)
            model.objects.filter(
                pk=lecture.pk,
                youtube_video_id=video_id,
                youtube_metadata_request_id=request_id,
            ).update(
                youtube_metadata_status=YOUTUBE_METADATA_STATUS_UNAVAILABLE,
                youtube_metadata_fetched_at=timezone.now(),
                updated_at=timezone.now(),
            )

    transaction.on_commit(publish_request, robust=True)
    return "queued"


# --- Duration-weighted progress (S2-05) --------------------------------------

# Contract for Group C1 (apps/students/selectors.py): both functions return
# the SAME shape and only published/released YouTube lectures with a saved
# duration participate; audio/text lectures are excluded until their own
# completion rule exists.

_PROGRESS_QUANTIZER = Decimal("0.01")


def _progress_lecture_filter():
    """Published/released lectures that can participate in duration weighting."""
    now = timezone.now()
    return (
        Q(is_published=True, youtube_duration_seconds__isnull=False)
        & Q(youtube_video_id__gt="")
        & (Q(release_at__isnull=True) | Q(release_at__lte=now))
    )


def _empty_progress_result():
    return {
        "percent": Decimal("0"),
        "completed_count": 0,
        "total_lectures": 0,
        "total_seconds": Decimal("0"),
        "completed_seconds": Decimal("0"),
    }


def _duration_weighted_progress(
    lecture_qs, completed_lecture_ids, *, duration_field="youtube_duration_seconds",
) -> dict:
    totals = lecture_qs.aggregate(total_seconds=Sum(duration_field))
    total_seconds = Decimal(str(totals["total_seconds"] or 0))
    completed_qs = lecture_qs.filter(id__in=completed_lecture_ids)
    completed_seconds = Decimal(
        str(completed_qs.aggregate(completed=Sum(duration_field))["completed"] or 0)
    )
    percent = (completed_seconds / total_seconds * 100).quantize(_PROGRESS_QUANTIZER) if total_seconds else Decimal("0")
    return {
        "percent": percent,
        "completed_count": len(list(completed_lecture_ids)),
        "total_lectures": lecture_qs.count(),
        "total_seconds": total_seconds,
        "completed_seconds": completed_seconds,
    }


def course_progress_for_enrollment(student_profile, course_version) -> dict:
    """Duration-weighted course progress using the course completion predicate.

    Callers must check ``can_access_course`` before rendering; this selector
    itself returns a zero result when no enrollment exists.
    """
    lecture_qs = (
        course_version.lectures.filter(
            Q(release_at__isnull=True) | Q(release_at__lte=timezone.now()),
            is_published=True,
        )
        .annotate(
            progress_duration_seconds=Coalesce(
                "youtube_duration_seconds", "audio_duration_seconds",
                Value(0), output_field=IntegerField(),
            ),
        )
    )
    enrollment = CourseEnrollment.objects.filter(
        student=student_profile, course_version=course_version,
    ).first()
    if enrollment is None:
        return _empty_progress_result()
    completed_lecture_ids = [
        lecture_id
        for lecture_id, done in _course_lecture_completion_map(
            student_profile, enrollment, lecture_qs
        ).items()
        if done
    ]
    return _duration_weighted_progress(
        lecture_qs, completed_lecture_ids, duration_field="progress_duration_seconds",
    )


def _active_course_quizzes(course_version):
    return Assessment.objects.filter(
        course_version=course_version,
        assessment_type=ASSESSMENT_TYPE_QUIZ,
        is_active=True,
        legacy_status=LEGACY_STATUS_ACTIVE,
    ).order_by("open_at", "id")


def get_course_lecture_readiness(enrollment: CourseEnrollment) -> dict:
    """Return published/released lecture completion for one course enrollment."""
    now = timezone.now()
    lectures = list(
        enrollment.course_version.lectures.filter(
            Q(release_at__isnull=True) | Q(release_at__lte=now),
            is_published=True,
        ).order_by("order")
    )
    completion_map = _course_lecture_completion_map(
        enrollment.student,
        enrollment,
        lectures,
    )
    completed = sum(1 for done in completion_map.values() if done)
    remaining = [
        lecture for lecture in lectures
        if not completion_map.get(lecture.id, False)
    ]
    return {
        "ready": all(completion_map.values()),
        "lectures_total": len(lectures),
        "lectures_completed": completed,
        "remaining_lectures": remaining,
        "completion_map": completion_map,
        "lectures": lectures,
    }


def get_course_completion_readiness(enrollment: CourseEnrollment) -> dict:
    """Compute lecture and quiz readiness for one course enrollment.

    All published/released lectures must be completed. Every active course quiz
    must have a latest attempt marked as passed, and the course must have one
    active designated end-course exam that is passed.
    """
    course_version = enrollment.course_version
    lecture_readiness = get_course_lecture_readiness(enrollment)
    total_lectures = lecture_readiness["lectures_total"]
    completed_lectures = lecture_readiness["lectures_completed"]
    all_lectures_completed = lecture_readiness["ready"]

    required_quizzes = list(_active_course_quizzes(course_version))
    latest_attempts = {}
    for attempt in AssessmentAttempt.objects.filter(
        student=enrollment.student.user,
        assessment__in=required_quizzes,
    ).order_by("assessment_id", "-attempt_number", "-id"):
        latest_attempts.setdefault(attempt.assessment_id, attempt)

    quiz_rows = []
    for quiz in required_quizzes:
        attempt = latest_attempts.get(quiz.id)
        quiz_rows.append({
            "assessment": quiz,
            "attempt": attempt,
            "passed": bool(attempt and attempt.is_pass is True),
        })
    all_quizzes_passed = all(row["passed"] for row in quiz_rows)

    completion_exam = Assessment.objects.filter(
        course_version=course_version,
        is_completion_exam=True,
    ).first()
    completion_exam_active = bool(
        completion_exam
        and completion_exam.is_active
        and completion_exam.legacy_status == LEGACY_STATUS_ACTIVE
    )
    completion_exam_passed = bool(
        completion_exam
        and any(
            row["assessment"].id == completion_exam.id and row["passed"]
            for row in quiz_rows
        )
    )

    lecture_timestamps = []
    lecture_ids = list(lecture_readiness["completion_map"])
    if lecture_ids:
        lecture_timestamps.extend(
            LectureWatchProgress.objects.filter(
                student=enrollment.student,
                course_enrollment=enrollment,
                lecture_id__in=lecture_ids,
                status=WATCH_STATUS_COMPLETED,
                completed_at__isnull=False,
            ).values_list("completed_at", flat=True)
        )
        lecture_timestamps.extend(
            LectureAudioProgress.objects.filter(
                student=enrollment.student,
                course_enrollment=enrollment,
                lecture_id__in=lecture_ids,
                status=WATCH_STATUS_COMPLETED,
                completed_at__isnull=False,
            ).values_list("completed_at", flat=True)
        )

    quiz_timestamps = [
        row["attempt"].graded_at or row["attempt"].submitted_at
        for row in quiz_rows
        if row["passed"] and row["attempt"] is not None
    ]
    timestamps = [stamp for stamp in [*lecture_timestamps, *quiz_timestamps] if stamp]
    last_requirement_at = max(timestamps) if timestamps else None

    block_reasons = []
    remaining_lectures = total_lectures - completed_lectures
    if remaining_lectures:
        block_reasons.append(
            _("%(count)s course lecture(s) are not completed.")
            % {"count": remaining_lectures}
        )
    failed_quizzes = [
        row["assessment"].title for row in quiz_rows if not row["passed"]
    ]
    if failed_quizzes:
        block_reasons.append(
            _("Required quizzes not passed: %(quizzes)s")
            % {"quizzes": ", ".join(failed_quizzes)}
        )
    if completion_exam is None:
        block_reasons.append(_("No end-course exam is configured for this course."))
    elif not completion_exam_active:
        block_reasons.append(_("The configured end-course exam is not active."))

    return {
        "ready": (
            all_lectures_completed
            and all_quizzes_passed
            and completion_exam_active
            and completion_exam_passed
        ),
        "lectures_total": total_lectures,
        "lectures_completed": completed_lectures,
        "all_lectures_completed": all_lectures_completed,
        "quizzes": quiz_rows,
        "quizzes_total": len(quiz_rows),
        "quizzes_passed": sum(1 for row in quiz_rows if row["passed"]),
        "all_quizzes_passed": all_quizzes_passed,
        "completion_exam": completion_exam,
        "completion_exam_active": completion_exam_active,
        "completion_exam_passed": completion_exam_passed,
        "last_requirement_at": last_requirement_at,
        "block_reasons": block_reasons,
    }


def _schedule_course_certificate_issuance(enrollment: CourseEnrollment, *, actor) -> None:
    """Issue the course certificate after the completion transaction commits."""

    def _issue():
        # Lazy import: certificates.services imports academics models.
        from apps.certificates.services import issue_course_certificate

        try:
            issue_course_certificate(
                enrollment.student,
                enrollment,
                actor=actor,
                source="auto_course_completion",
            )
        except ValueError as exc:
            logger.info(
                "Course certificate auto-issuance skipped for enrollment %s: %s",
                enrollment.pk,
                exc,
            )
        except Exception:
            logger.exception(
                "Course certificate auto-issuance failed for enrollment %s",
                enrollment.pk,
            )

    transaction.on_commit(_issue, robust=True)


def mark_course_completed(
    enrollment: CourseEnrollment,
    *,
    actor=None,
    notes: str = "",
) -> CourseEnrollment:
    """Mark a course completed once every lecture and active quiz requirement passes."""
    with transaction.atomic():
        locked = CourseEnrollment.objects.select_for_update().select_related(
            "student__user",
            "course_version__course",
        ).get(pk=enrollment.pk)
        if locked.status == ENROLLMENT_STATUS_COMPLETED:
            raise ValueError(_("This course enrollment is already completed."))
        readiness = get_course_completion_readiness(locked)
        if not readiness["ready"]:
            raise ValueError(_("Course completion requirements are not met."))
        completed_at = readiness["last_requirement_at"] or timezone.now()
        previous_status = locked.status
        locked.status = ENROLLMENT_STATUS_COMPLETED
        locked.completed_at = completed_at
        locked.completed_by = actor
        locked.completion_notes = notes
        locked.save(update_fields=[
            "status", "completed_at", "completed_by", "completion_notes", "updated_at",
        ])
        record_audit_event(
            actor=actor,
            action=ACTION_ENROLLMENT_CHANGE,
            target_type="course_enrollment",
            target_id=str(locked.id),
            section=SECTION_ACADEMICS,
            target_label=f"{locked.student.full_name} · {locked.course_version.course.title}",
            subject_type="student",
            subject_id=str(locked.student_id),
            subject_label=locked.student.full_name,
            metadata={
                "type": "course_completed",
                "changed_fields": ["status", "completed_at", "completed_by", "completion_notes"],
                "student_id": locked.student_id,
                "course_id": locked.course_version.course_id,
                "course_version_id": locked.course_version_id,
                "previous_status": previous_status,
                "new_status": locked.status,
                "completed_at": completed_at.isoformat(),
                "notes": notes,
            },
        )

    _schedule_course_certificate_issuance(locked, actor=actor)
    return locked


def diploma_progress_for_enrollment(student_profile, diploma_enrollment) -> dict:
    """Duration-weighted video progress scoped to the enrollment's cohort.

    Progress never leaks between cohorts: the lecture queryset is always
    filtered by ``diploma_enrollment.cohort`` and completion rows are filtered
    by the exact enrollment.
    """
    lecture_qs = DiplomaLecture.objects.filter(
        _progress_lecture_filter(), cohort=diploma_enrollment.cohort,
    )
    completed_lecture_ids = DiplomaLectureWatchProgress.objects.filter(
        lecture__in=lecture_qs,
        student=student_profile,
        diploma_enrollment=diploma_enrollment,
        status=WATCH_STATUS_COMPLETED,
    ).values_list("lecture_id", flat=True)
    return _duration_weighted_progress(lecture_qs, completed_lecture_ids)


def diploma_progress_for_enrollments(student_profile, diploma_enrollments) -> dict[int, dict]:
    """Return duration-weighted Diploma progress with set-based queries."""
    enrollments = list(diploma_enrollments)
    if not enrollments:
        return {}

    enrollment_ids = [enrollment.id for enrollment in enrollments]
    cohort_ids = {enrollment.cohort_id for enrollment in enrollments}
    lecture_totals_by_cohort = {
        row["cohort_id"]: row
        for row in DiplomaLecture.objects.filter(
            _progress_lecture_filter(),
            cohort_id__in=cohort_ids,
        ).values("cohort_id").annotate(
            total_seconds=Sum("youtube_duration_seconds"),
            total_lectures=Count("id"),
        )
    }
    eligible_lecture_ids = DiplomaLecture.objects.filter(
        _progress_lecture_filter(),
        cohort_id__in=cohort_ids,
    ).values("id")
    completed_by_enrollment = {
        row["diploma_enrollment_id"]: row
        for row in DiplomaLectureWatchProgress.objects.filter(
            student=student_profile,
            diploma_enrollment_id__in=enrollment_ids,
            lecture_id__in=eligible_lecture_ids,
            lecture__cohort_id=F("diploma_enrollment__cohort_id"),
            status=WATCH_STATUS_COMPLETED,
        ).values("diploma_enrollment_id").annotate(
            completed_seconds=Sum("lecture__youtube_duration_seconds"),
            completed_count=Count("lecture_id"),
        )
    }

    results = {}
    for enrollment in enrollments:
        lecture_totals = lecture_totals_by_cohort.get(enrollment.cohort_id, {})
        completed = completed_by_enrollment.get(enrollment.id, {})
        total_seconds = Decimal(str(lecture_totals.get("total_seconds") or 0))
        completed_seconds = Decimal(str(completed.get("completed_seconds") or 0))
        results[enrollment.id] = {
            "percent": (
                (completed_seconds / total_seconds * 100).quantize(_PROGRESS_QUANTIZER)
                if total_seconds else Decimal("0")
            ),
            "completed_count": completed.get("completed_count", 0),
            "total_lectures": lecture_totals.get("total_lectures", 0),
            "total_seconds": total_seconds,
            "completed_seconds": completed_seconds,
        }
    return results


# --- Watch server ------------------------------------------------------------

DEFAULT_COMPLETION_THRESHOLD = Decimal("80.0")
DEFAULT_CHECKPOINT_INTERVAL_SECONDS = 5


def _parse_checkpoint_payload(payload):
    """Parse untrusted checkpoint JSON into bounded primitive values."""
    if not isinstance(payload, dict):
        return None, {"error": "invalid_checkpoint", "status": 400}
    session_id_str = payload.get("session_id", "")
    if not isinstance(session_id_str, str) or not session_id_str:
        return None, {"error": "missing_session_id", "status": 400}
    try:
        session_id = uuid.UUID(session_id_str)
        sequence = int(payload.get("sequence", 0))
        position = Decimal(str(payload.get("position", 0)))
        rate = Decimal(str(payload.get("rate", 1)))
    except (ValueError, TypeError, InvalidOperation):
        return None, {"error": "invalid_checkpoint", "status": 400}
    if sequence < 1 or position < 0 or rate <= 0 or not position.is_finite() or not rate.is_finite():
        return None, {"error": "invalid_checkpoint", "status": 400}
    player_state = payload.get("player_state", "")
    if not isinstance(player_state, str) or len(player_state) > 32:
        return None, {"error": "invalid_checkpoint", "status": 400}
    return (session_id, sequence, position, rate, player_state), None


def violation_user_message(reason: str) -> str:
    """Return a non-technical, student-facing explanation for a violation."""
    messages = {
        VIOLATION_SPEED: _(
            "The playback speed was too fast. Reload the page and continue at normal speed."
        ),
        VIOLATION_FORWARD_SKIP: _(
            "You skipped ahead in the lecture. Reload the page and continue in order from your last saved position."
        ),
        VIOLATION_INVALID_SEQUENCE: _(
            "The playback session was interrupted. Reload the page and continue from your last saved position."
        ),
        VIOLATION_INVALID_POSITION: _(
            "The playback position changed unexpectedly. Reload the page and continue from your last saved position."
        ),
    }
    return str(messages.get(
        reason,
        _("Playback was stopped because a viewing rule was broken. Reload the page and continue from your last saved position."),
    ))


def checkpoint_error_user_message(error: str) -> str:
    """Return a non-technical, student-facing explanation for a checkpoint error."""
    if error in {"no_access", "enrollment_not_active", "enrollment_expired", "forbidden"}:
        return str(_("You no longer have access to this lecture."))
    if error == "unknown_session":
        return str(_("Your playback session expired. Reload the page and continue."))
    return str(_("Your progress could not be saved. Reload the page and try again."))


def start_lecture_watch(*, student, lecture):
    """Start or resume a watch session for a lecture.
    Returns dict with session_id, resume_position, duration, interval, threshold."""
    # Duration comes ONLY from persisted metadata; never from the client and
    # never from a remote fetch in the request path (S2-03).
    duration = lecture.youtube_duration_seconds or 0
    if duration <= 0:
        raise ValueError(_("Video metadata is still being processed."))

    version = lecture.course_version
    if not can_access_lecture(student, lecture):
        raise ValueError(_("This lecture is not available."))

    threshold = version.course.video_completion_percent or 80

    with transaction.atomic():
        enrollment = CourseEnrollment.objects.select_for_update().filter(
            student=student, course_version=version, status=ENROLLMENT_STATUS_ACTIVE,
        ).first()
        if enrollment is None or enrollment.is_access_expired:
            raise ValueError(_("No active enrollment."))
        lwp = (
            LectureWatchProgress.objects
            .select_for_update()
            .filter(student=student, lecture=lecture, course_enrollment=enrollment)
            .first()
        )
        if lwp is None:
            lwp = LectureWatchProgress(
                student=student,
                lecture=lecture,
                course_enrollment=enrollment,
            )
        # Preserve completed
        if lwp.status == WATCH_STATUS_COMPLETED:
            return {
                "session_id": str(lwp.active_session_id),
                "resume_position": float(lwp.resume_position_seconds),
                "duration": duration,
                "interval": DEFAULT_CHECKPOINT_INTERVAL_SECONDS,
                "threshold": float(threshold),
                "completed": True,
            }
        # A violation blocks only the session in which it happened. Keep the
        # validated progress and resume from the last server-accepted position.
        resume_pos = lwp.resume_position_seconds
        lwp.status = WATCH_STATUS_IN_PROGRESS
        lwp.violation_reason = VIOLATION_NONE
        lwp.last_sequence = 0
        lwp.last_position_seconds = resume_pos
        lwp.last_checkpoint_at = timezone.now()
        session_id = uuid.uuid4()
        lwp.active_session_id = session_id
        lwp.save()

    return {
        "session_id": str(session_id),
        "resume_position": float(resume_pos),
        "duration": duration,
        "interval": DEFAULT_CHECKPOINT_INTERVAL_SECONDS,
        "threshold": float(threshold),
        "completed": False,
    }


def _validated_progress_pct(lwp, duration: Decimal) -> Decimal:
    """Percent completed from furthest continuously validated seconds, capped at 100."""
    return min(lwp.validated_contiguous_seconds / duration * 100, Decimal("100"))


def record_lecture_checkpoint(*, student, lecture, payload, now=None):
    """Record a heartbeat checkpoint. Returns dict with progress, status, violation.
    payload must contain: session_id, sequence, position, player_state, rate.

    Duration comes ONLY from persisted lecture metadata and completion is
    computed ONLY from furthest continuously validated seconds; a client can
    never manufacture completion with a reported position or duration (S2-04).
    """
    if now is None:
        now = timezone.now()

    parsed, error = _parse_checkpoint_payload(payload)
    if error:
        return error
    session_id, sequence, position, rate, player_state = parsed

    if not can_access_lecture(student, lecture):
        return {"error": "no_access", "status": 403}

    duration = Decimal(str(lecture.youtube_duration_seconds or 1))
    duration_known = lecture.youtube_duration_seconds is not None

    version = lecture.course_version
    threshold = Decimal(str(version.course.video_completion_percent or 80))

    with transaction.atomic():
        enrollment = CourseEnrollment.objects.select_for_update().filter(
            student=student, course_version=version, status=ENROLLMENT_STATUS_ACTIVE,
        ).first()
        if enrollment is None or enrollment.is_access_expired:
            return {"error": "no_access", "status": 403}
        lwp = (
            LectureWatchProgress.objects
            .select_for_update()
            .filter(
                student=student, lecture=lecture,
                course_enrollment=enrollment, active_session_id=session_id,
            )
            .first()
        )
        if lwp is None:
            return {"error": "unknown_session", "status": 404}
        if lwp.status == WATCH_STATUS_COMPLETED:
            return {
                "progress": 100.0,
                "status": WATCH_STATUS_COMPLETED,
                "violation": VIOLATION_NONE,
                "resume_position": float(lwp.resume_position_seconds),
            }
        # A violated session is blocked; the next session starts fresh but
        # keeps the progress already validated for this lecture.
        if lwp.status == WATCH_STATUS_VIOLATED:
            return {
                "error": "session_violated",
                "status": 409,
                "violation": lwp.violation_reason,
                "progress": float(_validated_progress_pct(lwp, duration)),
            }

        # Sequence validation
        expected_seq = lwp.last_sequence + 1
        if sequence != expected_seq:
            lwp.status = WATCH_STATUS_VIOLATED
            lwp.violation_reason = VIOLATION_INVALID_SEQUENCE
            lwp.save(update_fields=["status", "violation_reason"])
            return {
                "error": "out_of_order",
                "status": 409,
                "violation": VIOLATION_INVALID_SEQUENCE,
                "progress": float(_validated_progress_pct(lwp, duration)),
            }

        # Time movement validation
        elapsed = Decimal("0")
        if lwp.last_checkpoint_at:
            diff = (now - lwp.last_checkpoint_at).total_seconds()
            elapsed = Decimal(str(diff))

        # Speed check (first so it's never masked by skip detection)
        if rate > Decimal("1.5"):
            lwp.status = WATCH_STATUS_VIOLATED
            lwp.violation_reason = VIOLATION_SPEED
            lwp.save(update_fields=["status", "violation_reason"])
            return {
                "error": "speed_violation",
                "status": 409,
                "violation": VIOLATION_SPEED,
                "progress": float(_validated_progress_pct(lwp, duration)),
            }

        allowed_jitter = Decimal(str(DEFAULT_CHECKPOINT_INTERVAL_SECONDS + 2))
        if elapsed < Decimal("1") and lwp.last_sequence > 0:
            # Too fast
            lwp.status = WATCH_STATUS_VIOLATED
            lwp.violation_reason = VIOLATION_INVALID_POSITION
            lwp.save(update_fields=["status", "violation_reason"])
            return {
                "error": "too_fast",
                "status": 409,
                "violation": VIOLATION_INVALID_POSITION,
                "progress": float(_validated_progress_pct(lwp, duration)),
            }

        # Forward skip detection (re-watching before checkpoint is fine)
        position_delta = position - lwp.last_position_seconds
        if position_delta > allowed_jitter and lwp.last_sequence > 0 and position > lwp.last_position_seconds:
            lwp.status = WATCH_STATUS_VIOLATED
            lwp.violation_reason = VIOLATION_FORWARD_SKIP
            lwp.save(update_fields=["status", "violation_reason"])
            return {
                "error": "forward_skip",
                "status": 409,
                "violation": VIOLATION_FORWARD_SKIP,
                "progress": float(_validated_progress_pct(lwp, duration)),
            }

        # Advance validated seconds (only for forward movement within video)
        if duration_known and position > lwp.last_position_seconds and position <= duration:
            advance = min(position - lwp.last_position_seconds, elapsed + Decimal("2"))
            lwp.validated_contiguous_seconds += advance

        lwp.last_sequence = sequence
        lwp.last_position_seconds = position
        lwp.last_player_state = player_state
        lwp.last_checkpoint_at = now
        lwp.resume_position_seconds = position

        # Completion derives ONLY from furthest continuously validated seconds
        # divided by server-known duration, capped at 100 (S2-04).
        progress_pct = float(_validated_progress_pct(lwp, duration)) if duration_known else 0.0
        completed = False
        if duration_known and Decimal(str(progress_pct)) >= threshold and lwp.status != WATCH_STATUS_COMPLETED:
            lwp.status = WATCH_STATUS_COMPLETED
            lwp.completed_at = now
            completed = True

        lwp.save()

    return {
        "progress": progress_pct,
        "status": lwp.status,
        "violation": lwp.violation_reason,
        "resume_position": float(lwp.resume_position_seconds),
        "completed": completed,
    }


# --- Audio watch server (P5-01) ----------------------------------------------
#
# Audio completion participates in the course sequential predicate exactly like
# video completion. Duration comes ONLY from persisted transcode metadata and
# completion is computed ONLY from server-validated contiguous seconds; the
# client can never manufacture completion (S2-04 rule applied to audio).

AUDIO_COMPLETION_THRESHOLD = Decimal("80.0")


def start_lecture_audio(*, student, lecture):
    """Start or resume an audio playback session for a course lecture."""
    if not can_access_lecture(student, lecture):
        raise ValueError(_("This lecture is not available."))
    if not _lecture_has_audio(lecture):
        raise ValueError(_("No audio is available for this lecture."))

    duration = Decimal(str(lecture.audio_duration_seconds))
    version = lecture.course_version
    threshold = Decimal(str(version.course.video_completion_percent or 80))

    with transaction.atomic():
        enrollment = CourseEnrollment.objects.select_for_update().filter(
            student=student, course_version=version, status=ENROLLMENT_STATUS_ACTIVE,
        ).first()
        if enrollment is None or enrollment.is_access_expired:
            raise ValueError(_("No active enrollment."))
        lap = (
            LectureAudioProgress.objects
            .select_for_update()
            .filter(student=student, lecture=lecture, course_enrollment=enrollment)
            .first()
        )
        if lap is None:
            lap = LectureAudioProgress(
                student=student,
                lecture=lecture,
                course_enrollment=enrollment,
            )
        if lap.status == WATCH_STATUS_COMPLETED:
            return {
                "session_id": str(lap.active_session_id),
                "resume_position": float(lap.resume_position_seconds),
                "duration": float(duration),
                "interval": DEFAULT_CHECKPOINT_INTERVAL_SECONDS,
                "threshold": float(threshold),
                "completed": True,
            }
        # A violation blocks only the session in which it happened. Keep the
        # validated progress and resume from the last server-accepted position.
        resume_pos = lap.resume_position_seconds
        lap.status = WATCH_STATUS_IN_PROGRESS
        lap.violation_reason = VIOLATION_NONE
        lap.last_sequence = 0
        lap.last_position_seconds = resume_pos
        lap.last_checkpoint_at = timezone.now()
        session_id = uuid.uuid4()
        lap.active_session_id = session_id
        lap.save()

    return {
        "session_id": str(session_id),
        "resume_position": float(resume_pos),
        "duration": float(duration),
        "interval": DEFAULT_CHECKPOINT_INTERVAL_SECONDS,
        "threshold": float(threshold),
        "completed": False,
    }


def record_lecture_audio_checkpoint(*, student, lecture, payload, now=None):
    """Record a heartbeat checkpoint for audio playback.

    payload must contain: session_id, sequence, position, player_state, rate.
    Mirrors the video checkpoint validation: no client-trusted duration, no
    manufactured completion, and the violated session is blocked until a new
    session starts. Validated progress is preserved across sessions.
    """
    if now is None:
        now = timezone.now()

    parsed, error = _parse_checkpoint_payload(payload)
    if error:
        return error
    session_id, sequence, position, rate, _player_state = parsed

    if not can_access_lecture(student, lecture):
        return {"error": "no_access", "status": 403}

    duration = Decimal(str(lecture.audio_duration_seconds or 1))
    duration_known = lecture.audio_duration_seconds is not None

    version = lecture.course_version
    threshold = Decimal(str(version.course.video_completion_percent or 80))

    with transaction.atomic():
        enrollment = CourseEnrollment.objects.select_for_update().filter(
            student=student, course_version=version, status=ENROLLMENT_STATUS_ACTIVE,
        ).first()
        if enrollment is None or enrollment.is_access_expired:
            return {"error": "no_access", "status": 403}
        lap = (
            LectureAudioProgress.objects
            .select_for_update()
            .filter(
                student=student, lecture=lecture,
                course_enrollment=enrollment, active_session_id=session_id,
            )
            .first()
        )
        if lap is None:
            return {"error": "unknown_session", "status": 404}
        if lap.status == WATCH_STATUS_COMPLETED:
            return {
                "progress": 100.0,
                "status": WATCH_STATUS_COMPLETED,
                "violation": VIOLATION_NONE,
                "resume_position": float(lap.resume_position_seconds),
            }
        if lap.status == WATCH_STATUS_VIOLATED:
            return {
                "error": "session_violated",
                "status": 409,
                "violation": lap.violation_reason,
                "progress": float(_validated_progress_pct(lap, duration)),
            }

        # Sequence validation
        expected_seq = lap.last_sequence + 1
        if sequence != expected_seq:
            lap.status = WATCH_STATUS_VIOLATED
            lap.violation_reason = VIOLATION_INVALID_SEQUENCE
            lap.save(update_fields=["status", "violation_reason"])
            return {
                "error": "out_of_order",
                "status": 409,
                "violation": VIOLATION_INVALID_SEQUENCE,
                "progress": float(_validated_progress_pct(lap, duration)),
            }

        # Time movement validation
        elapsed = Decimal("0")
        if lap.last_checkpoint_at:
            diff = (now - lap.last_checkpoint_at).total_seconds()
            elapsed = Decimal(str(diff))

        # Speed check
        if rate > Decimal("1.5"):
            lap.status = WATCH_STATUS_VIOLATED
            lap.violation_reason = VIOLATION_SPEED
            lap.save(update_fields=["status", "violation_reason"])
            return {
                "error": "speed_violation",
                "status": 409,
                "violation": VIOLATION_SPEED,
                "progress": float(_validated_progress_pct(lap, duration)),
            }

        allowed_jitter = Decimal(str(DEFAULT_CHECKPOINT_INTERVAL_SECONDS + 2))
        if elapsed < Decimal("1") and lap.last_sequence > 0:
            lap.status = WATCH_STATUS_VIOLATED
            lap.violation_reason = VIOLATION_INVALID_POSITION
            lap.save(update_fields=["status", "violation_reason"])
            return {
                "error": "too_fast",
                "status": 409,
                "violation": VIOLATION_INVALID_POSITION,
                "progress": float(_validated_progress_pct(lap, duration)),
            }

        # Forward skip detection
        position_delta = position - lap.last_position_seconds
        if position_delta > allowed_jitter and lap.last_sequence > 0 and position > lap.last_position_seconds:
            lap.status = WATCH_STATUS_VIOLATED
            lap.violation_reason = VIOLATION_FORWARD_SKIP
            lap.save(update_fields=["status", "violation_reason"])
            return {
                "error": "forward_skip",
                "status": 409,
                "violation": VIOLATION_FORWARD_SKIP,
                "progress": float(_validated_progress_pct(lap, duration)),
            }

        # Advance validated seconds (only for forward movement within audio)
        if duration_known and position > lap.last_position_seconds and position <= duration:
            advance = min(position - lap.last_position_seconds, elapsed + Decimal("2"))
            lap.validated_contiguous_seconds += advance

        lap.last_sequence = sequence
        lap.last_position_seconds = position
        lap.last_checkpoint_at = now
        lap.resume_position_seconds = position

        progress_pct = float(_validated_progress_pct(lap, duration)) if duration_known else 0.0
        completed = False
        if duration_known and Decimal(str(progress_pct)) >= threshold and lap.status != WATCH_STATUS_COMPLETED:
            lap.status = WATCH_STATUS_COMPLETED
            lap.completed_at = now
            completed = True

        lap.save()

    return {
        "progress": progress_pct,
        "status": lap.status,
        "violation": lap.violation_reason,
        "resume_position": float(lap.resume_position_seconds),
        "completed": completed,
    }


def get_visible_diploma_lectures(student_profile, enrollment, semester=None, *, access=None):
    """Return published, released lectures from visible semesters only."""
    if enrollment.status != ENROLLMENT_STATUS_ACTIVE:
        return DiplomaLecture.objects.none()
    if enrollment.expires_at and timezone.now() >= enrollment.expires_at:
        return DiplomaLecture.objects.none()
    access = access or get_student_diploma_access(enrollment)
    visible_semester_ids = {
        item.id for item in access["visible_semesters"]
    }
    if semester is not None:
        if semester.id not in visible_semester_ids:
            return DiplomaLecture.objects.none()
        visible_semester_ids = {semester.id}
    if not visible_semester_ids:
        return DiplomaLecture.objects.none()
    qs = DiplomaLecture.objects.filter(
        cohort=enrollment.cohort,
        semester_id__in=visible_semester_ids,
        is_published=True,
    ).filter(
        Q(release_at__isnull=True) | Q(release_at__lte=timezone.now()),
    )
    return qs.order_by("order").select_related("semester")


def can_access_diploma_lecture(student_profile, enrollment, lecture) -> bool:
    """Check whether the student can view a specific diploma lecture."""
    if enrollment.status != ENROLLMENT_STATUS_ACTIVE:
        return False
    if enrollment.expires_at and timezone.now() >= enrollment.expires_at:
        return False
    if lecture.cohort_id != enrollment.cohort_id:
        return False
    if lecture.semester_id not in {
        item.id for item in get_student_diploma_access(enrollment)["visible_semesters"]
    }:
        return False
    return lecture.is_released


def start_diploma_lecture_watch(*, student, enrollment, lecture):
    """Start or resume a watch session for a diploma lecture."""
    if not lecture.cohort.diploma.video_tracking_enabled:
        raise ValueError(_("Video tracking is disabled for this diploma."))

    if not can_access_diploma_lecture(student, enrollment, lecture):
        raise ValueError(_("This lecture is not available."))

    # Duration comes ONLY from persisted metadata; never from a remote fetch
    # or the client in the request path (S2-03).
    duration = lecture.youtube_duration_seconds or 0
    if duration <= 0:
        raise ValueError(_("Video metadata is still being processed."))

    threshold = Decimal(str(lecture.cohort.diploma.video_completion_percent or 80))

    with transaction.atomic():
        enrollment = DiplomaEnrollment.objects.select_for_update().get(
            pk=enrollment.pk, student=student,
        )
        if not can_access_diploma_lecture(student, enrollment, lecture):
            raise ValueError(_("This lecture is not available."))
        lwp = (
            DiplomaLectureWatchProgress.objects
            .select_for_update()
            .filter(student=student, lecture=lecture, diploma_enrollment=enrollment)
            .first()
        )
        if lwp is None:
            lwp = DiplomaLectureWatchProgress(
                student=student,
                lecture=lecture,
                diploma_enrollment=enrollment,
            )
        if lwp.status == WATCH_STATUS_COMPLETED:
            return {
                "session_id": str(lwp.active_session_id),
                "resume_position": float(lwp.resume_position_seconds),
                "duration": duration,
                "interval": DEFAULT_CHECKPOINT_INTERVAL_SECONDS,
                "threshold": float(threshold),
                "completed": True,
            }
        # A violation blocks only the session in which it happened. Keep the
        # validated progress and resume from the last server-accepted position.
        resume_pos = lwp.resume_position_seconds
        lwp.status = WATCH_STATUS_IN_PROGRESS
        lwp.violation_reason = VIOLATION_NONE
        lwp.last_sequence = 0
        lwp.last_position_seconds = resume_pos
        lwp.last_checkpoint_at = timezone.now()
        session_id = uuid.uuid4()
        lwp.active_session_id = session_id
        lwp.save()

    return {
        "session_id": str(session_id),
        "resume_position": float(resume_pos),
        "duration": duration,
        "interval": DEFAULT_CHECKPOINT_INTERVAL_SECONDS,
        "threshold": float(threshold),
        "completed": False,
    }


def record_diploma_lecture_checkpoint(*, student, enrollment, lecture, payload, now=None):
    """Record a heartbeat checkpoint for a diploma lecture.

    Same S2-04 rules as ``record_lecture_checkpoint``: duration from persisted
    metadata only, completion from furthest validated seconds, checkpoints for
    a violated session rejected, and authorization/enrollment re-checked here.
    """
    if now is None:
        now = timezone.now()

    parsed, error = _parse_checkpoint_payload(payload)
    if error:
        return error
    session_id, sequence, position, rate, player_state = parsed

    if not can_access_diploma_lecture(student, enrollment, lecture):
        return {"error": "no_access", "status": 403}

    duration = Decimal(str(lecture.youtube_duration_seconds or 1))
    duration_known = lecture.youtube_duration_seconds is not None

    threshold = Decimal(str(lecture.cohort.diploma.video_completion_percent or 80))

    with transaction.atomic():
        enrollment = DiplomaEnrollment.objects.select_for_update().get(
            pk=enrollment.pk, student=student,
        )
        if not can_access_diploma_lecture(student, enrollment, lecture):
            return {"error": "no_access", "status": 403}
        lwp = (
            DiplomaLectureWatchProgress.objects
            .select_for_update()
            .filter(
                student=student, lecture=lecture,
                diploma_enrollment=enrollment, active_session_id=session_id,
            )
            .first()
        )
        if lwp is None:
            return {"error": "unknown_session", "status": 404}
        if lwp.status == WATCH_STATUS_COMPLETED:
            return {
                "progress": 100.0,
                "status": WATCH_STATUS_COMPLETED,
                "violation": VIOLATION_NONE,
                "resume_position": float(lwp.resume_position_seconds),
            }
        if lwp.status == WATCH_STATUS_VIOLATED:
            return {
                "error": "session_violated",
                "status": 409,
                "violation": lwp.violation_reason,
                "progress": float(_validated_progress_pct(lwp, duration)),
            }

        # Sequence validation
        expected_seq = lwp.last_sequence + 1
        if sequence != expected_seq:
            lwp.status = WATCH_STATUS_VIOLATED
            lwp.violation_reason = VIOLATION_INVALID_SEQUENCE
            lwp.save(update_fields=["status", "violation_reason"])
            return {
                "error": "out_of_order",
                "status": 409,
                "violation": VIOLATION_INVALID_SEQUENCE,
                "progress": float(_validated_progress_pct(lwp, duration)),
            }

        # Time movement validation
        elapsed = Decimal("0")
        if lwp.last_checkpoint_at:
            diff = (now - lwp.last_checkpoint_at).total_seconds()
            elapsed = Decimal(str(diff))

        # Speed check
        if rate > Decimal("1.5"):
            lwp.status = WATCH_STATUS_VIOLATED
            lwp.violation_reason = VIOLATION_SPEED
            lwp.save(update_fields=["status", "violation_reason"])
            return {
                "error": "speed_violation",
                "status": 409,
                "violation": VIOLATION_SPEED,
                "progress": float(_validated_progress_pct(lwp, duration)),
            }

        allowed_jitter = Decimal(str(DEFAULT_CHECKPOINT_INTERVAL_SECONDS + 2))
        if elapsed < Decimal("1") and lwp.last_sequence > 0:
            lwp.status = WATCH_STATUS_VIOLATED
            lwp.violation_reason = VIOLATION_INVALID_POSITION
            lwp.save(update_fields=["status", "violation_reason"])
            return {
                "error": "too_fast",
                "status": 409,
                "violation": VIOLATION_INVALID_POSITION,
                "progress": float(_validated_progress_pct(lwp, duration)),
            }

        # Forward skip detection
        position_delta = position - lwp.last_position_seconds
        if position_delta > allowed_jitter and lwp.last_sequence > 0 and position > lwp.last_position_seconds:
            lwp.status = WATCH_STATUS_VIOLATED
            lwp.violation_reason = VIOLATION_FORWARD_SKIP
            lwp.save(update_fields=["status", "violation_reason"])
            return {
                "error": "forward_skip",
                "status": 409,
                "violation": VIOLATION_FORWARD_SKIP,
                "progress": float(_validated_progress_pct(lwp, duration)),
            }

        # Advance validated seconds
        if duration_known and position > lwp.last_position_seconds and position <= duration:
            advance = min(position - lwp.last_position_seconds, elapsed + Decimal("2"))
            lwp.validated_contiguous_seconds += advance

        lwp.last_sequence = sequence
        lwp.last_position_seconds = position
        lwp.last_player_state = player_state
        lwp.last_checkpoint_at = now
        lwp.resume_position_seconds = position

        validated_pct = _validated_progress_pct(lwp, duration) if duration_known else Decimal("0")
        progress_pct = float(validated_pct)
        completed = False
        if duration_known and validated_pct >= threshold and lwp.status != WATCH_STATUS_COMPLETED:
            lwp.status = WATCH_STATUS_COMPLETED
            lwp.completed_at = now
            completed = True

        lwp.save()

    return {
        "progress": progress_pct,
        "status": lwp.status,
        "violation": lwp.violation_reason,
        "resume_position": float(lwp.resume_position_seconds),
        "completed": completed,
    }
