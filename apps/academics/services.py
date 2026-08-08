"""Access policy and enrollment business logic for academics."""

import logging
import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

from apps.academics.constants import (
    ENROLLMENT_STATUS_ACTIVE,
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
)
from apps.academics.models import (
    Cohort,
    Course,
    CourseEnrollment,
    CourseVersion,
    DiplomaEnrollment,
    DiplomaLecture,
    DiplomaLectureWatchProgress,
    Lecture,
    LectureWatchProgress,
    Semester,
    ModeratorAssignment,
    ModeratorStudentAssignment,
    TeacherAssignment,
)
from apps.accounts.models import User
from apps.audit.services import record_audit_event
from apps.progression.constants import (
    COMPONENT_TYPE_ATTENDANCE,
    COMPONENT_TYPE_HOMEWORK_ASSESSMENT,
    COMPONENT_TYPE_SEMESTER_EXAM,
    PROMOTION_DECISION_ACADEMIC_YEAR_PROMOTE,
    SEMESTER_PROGRESS_COMPLETED,
    SEMESTER_PROGRESS_IN_PROGRESS,
    SEMESTER_PROGRESS_NOT_STARTED,
    SEMESTER_PROGRESS_PASSED,
    SEMESTER_PROGRESS_PASSED_CONFIRMED,
    SEMESTER_PROGRESS_PROMOTED,
)
from apps.progression.models import (
    CohortAcademicYear,
    PromotionDecision,
    SemesterGradeComponent,
    StudentSemesterProgress,
)


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
    """Mark any active enrollments whose expiry has passed as expired."""
    now = timezone.now()
    DiplomaEnrollment.objects.filter(
        status=ENROLLMENT_STATUS_ACTIVE,
        expires_at__lte=now,
    ).update(status=ENROLLMENT_STATUS_EXPIRED)
    CourseEnrollment.objects.filter(
        status=ENROLLMENT_STATUS_ACTIVE,
        expires_at__lte=now,
    ).update(status=ENROLLMENT_STATUS_EXPIRED)


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


def get_student_diploma_access(enrollment):
    """Return only the completed and currently unlocked diploma semesters."""
    semesters = list(
        enrollment.cohort.diploma.semesters.filter(is_active=True).order_by("order")
    )
    progress = {
        item.semester_id: item
        for item in StudentSemesterProgress.objects.filter(diploma_enrollment=enrollment)
    }
    # TODO(phase5): remove 'passed' and 'completed' after migration
    completed_statuses = {
        SEMESTER_PROGRESS_COMPLETED,
        SEMESTER_PROGRESS_PASSED,
        SEMESTER_PROGRESS_PASSED_CONFIRMED,
        SEMESTER_PROGRESS_PROMOTED,
    }
    visible = []
    current = None
    blockers = []
    for semester in semesters:
        item = progress.get(semester.id)
        if item and item.status in completed_statuses:
            visible.append(semester)
            continue
        if current is None:
            if visible and _academic_year_promotion_pending(enrollment, visible[-1], semester):
                blockers.append("academic_year_promotion_pending")
                break
            current = semester
            visible.append(semester)
            if item and item.status not in {SEMESTER_PROGRESS_NOT_STARTED, SEMESTER_PROGRESS_IN_PROGRESS}:
                blockers.append(item.status)
        break
    return {
        "completed_semesters": visible[:-1] if current else visible,
        "current_semester": current,
        "visible_semesters": visible,
        "hidden_semesters": semesters[len(visible):],
        "blockers": blockers,
    }


def get_visible_semester_ids_by_enrollment(enrollments) -> dict[int, set[int]]:
    """Batch diploma semester visibility checks for dashboard and assessment lists."""
    enrollments = list(enrollments)
    if not enrollments:
        return {}

    enrollment_ids = [enrollment.id for enrollment in enrollments]
    semesters_by_diploma = {}
    semesters_by_id = {}
    for semester in Semester.objects.filter(
        diploma_id__in={e.cohort.diploma_id for e in enrollments}, is_active=True,
    ).order_by("order"):
        semesters_by_diploma.setdefault(semester.diploma_id, []).append(semester)
        semesters_by_id[semester.id] = semester

    progress_by_enrollment = {}
    for progress in StudentSemesterProgress.objects.filter(diploma_enrollment_id__in=enrollment_ids):
        progress_by_enrollment.setdefault(progress.diploma_enrollment_id, {})[progress.semester_id] = progress

    years_by_cohort = {}
    for year in CohortAcademicYear.objects.filter(
        cohort_id__in={e.cohort_id for e in enrollments},
    ).order_by("cohort_id", "year_number"):
        years_by_cohort.setdefault(year.cohort_id, []).append(year)

    promotions_by_enrollment = {}
    for promotion in PromotionDecision.objects.filter(
        diploma_enrollment_id__in=enrollment_ids,
        decision_type=PROMOTION_DECISION_ACADEMIC_YEAR_PROMOTE,
    ):
        promotions_by_enrollment.setdefault(promotion.diploma_enrollment_id, []).append(promotion)

    # TODO(phase5): remove 'passed' and 'completed' after migration
    completed_statuses = {
        SEMESTER_PROGRESS_COMPLETED,
        SEMESTER_PROGRESS_PASSED,
        SEMESTER_PROGRESS_PASSED_CONFIRMED,
        SEMESTER_PROGRESS_PROMOTED,
    }
    visible_by_enrollment = {}
    for enrollment in enrollments:
        visible_ids = []
        semesters = semesters_by_diploma.get(enrollment.cohort.diploma_id, [])
        progress = progress_by_enrollment.get(enrollment.id, {})
        years = years_by_cohort.get(enrollment.cohort_id, [])
        promotions = promotions_by_enrollment.get(enrollment.id, [])
        for semester in semesters:
            item = progress.get(semester.id)
            if item and item.status in completed_statuses:
                visible_ids.append(semester.id)
                continue
            if visible_ids and years:
                semesters_per_year = years[0].semesters_per_year
                previous = semesters_by_id[visible_ids[-1]]
                previous_year = ((previous.order - 1) // semesters_per_year) + 1
                current_year = ((semester.order - 1) // semesters_per_year) + 1
                if previous_year != current_year and not any(
                    p.from_semester_id == previous.id and p.to_semester_id == semester.id
                    for p in promotions
                ):
                    break
            visible_ids.append(semester.id)
            break
        visible_by_enrollment[enrollment.id] = set(visible_ids)
    return visible_by_enrollment


def _academic_year_promotion_pending(enrollment, previous_semester, next_semester):
    from apps.progression.models import CohortAcademicYear, PromotionDecision
    from apps.progression.constants import PROMOTION_DECISION_ACADEMIC_YEAR_PROMOTE

    periods = list(CohortAcademicYear.objects.filter(cohort=enrollment.cohort).order_by("year_number"))
    if not periods:
        return False
    previous_year = ((previous_semester.order - 1) // periods[0].semesters_per_year) + 1
    next_year = ((next_semester.order - 1) // periods[0].semesters_per_year) + 1
    if previous_year == next_year:
        return False
    return not PromotionDecision.objects.filter(
        diploma_enrollment=enrollment,
        from_semester=previous_semester,
        to_semester=next_semester,
        decision_type=PROMOTION_DECISION_ACADEMIC_YEAR_PROMOTE,
    ).exists()


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


def can_access_lecture(student_profile, lecture) -> bool:
    """Check whether the student can view a specific lecture."""
    if not can_access_course(student_profile, lecture.course_version):
        return False
    return lecture.is_released


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


def revoke_teacher_cohort_assignment(
    assignment: TeacherAssignment,
    actor,
    reason: str = "",
) -> TeacherAssignment:
    """Revoke a teacher assignment without deleting history (delegates to new model)."""
    return revoke_teacher_assignment(assignment, actor, reason)


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
       whole_diploma     â†’ diploma_id
      diploma_semester  â†’ semester_id
      diploma_cohort    â†’ cohort_id
      semester_cohort   â†’ semester_id + cohort_id
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
    """Create a cohort. Delegates to create_cohort_with_defaults â€” no CourseVersion rows created."""
    return create_cohort_with_defaults(diploma=diploma, form_data=form_data, actor=actor)


# --- Deletion services -------------------------------------------------------

# Delete-event action codes. apps/audit/constants.py is user-owned WIP outside
# this group's file list, so descriptive literals are passed to
# record_audit_event instead of new constants; add matching constants (plus an
# AlterField migration) there if audit-filter labels are wanted.
_ACTION_ASSIGNMENT_REMOVED = "academic.assignment_removed"
_ACTION_DIPLOMA_DELETED = "academic.diploma_deleted"
_ACTION_SEMESTER_DELETED = "academic.semester_deleted"
_ACTION_COURSE_DELETED = "academic.course_deleted"
_ACTION_COURSE_VERSION_DELETED = "academic.course_version_deleted"
_ACTION_LECTURE_DELETED = "academic.lecture_deleted"
_ACTION_DIPLOMA_LECTURE_DELETED = "academic.diploma_lecture_deleted"
_ACTION_COHORT_DELETED = "academic.cohort_deleted"


def _remove_scope_assignment_rows(queryset, *, actor, assignment_type):
    """Delete assignment rows that would raise ProtectedError and audit each removal.

    The academic deletion rules require blocking PROTECT scope rows to be deleted
    explicitly (with an audit event each) so the target delete never 500s; declared
    SET_NULL relations are left to the database.
    """
    removed = 0
    for assignment in queryset:
        record_audit_event(
            actor=actor,
            action=_ACTION_ASSIGNMENT_REMOVED,
            target_type=assignment_type,
            target_id=str(assignment.id),
            metadata={"scope_type": assignment.scope_type},
        )
        assignment.delete()
        removed += 1
    return removed


def _record_academic_delete(*, actor, action, target_type, target):
    record_audit_event(
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=str(target.id),
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
    from apps.storage.services import (
        record_deferred_storage_deletion,
        storage_service,
    )

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
            actor=actor, action=_ACTION_DIPLOMA_DELETED, target_type="diploma", target=diploma,
        )
        diploma.delete()
        _schedule_audio_cleanup(lecture_keys, prefixes)


def delete_semester_record(*, semester, actor):
    """Hard-delete a semester and its owned delivery content, preserving immutable history.

    Diploma-lecture audio objects are removed post-commit (S2-33); DB history
    stays separate from storage cleanup.
    """
    with transaction.atomic():
        lecture_rows = list(semester.diploma_lectures.values_list("id", "audio_storage_key"))
        lecture_keys = [row[1] for row in lecture_rows]
        prefixes = [_audio_prefix_for_lecture(row[0], diploma=True) for row in lecture_rows]
        _record_academic_delete(
            actor=actor, action=_ACTION_SEMESTER_DELETED, target_type="semester", target=semester,
        )
        semester.delete()
        _schedule_audio_cleanup(lecture_keys, prefixes)


def delete_course_record(*, course, actor):
    """Hard-delete a course with its versions and content, preserving immutable history.

    Replaces the former delete_diploma_course: course deletion also removes the
    course-version teacher/moderator scope rows that would raise ProtectedError.
    """
    with transaction.atomic():
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
            actor=actor, action=_ACTION_COURSE_DELETED, target_type="course", target=course,
        )
        course.delete()
        _schedule_audio_cleanup(lecture_keys, prefixes)


def delete_course_version_record(*, version, actor):
    """Hard-delete a course version with its content, preserving immutable history.

    Physical audio objects are removed post-commit (S2-33); DB history stays
    separate from storage cleanup.
    """
    with transaction.atomic():
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
            actor=actor, action=_ACTION_COURSE_VERSION_DELETED, target_type="course_version", target=version,
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
            actor=actor, action=_ACTION_LECTURE_DELETED, target_type="lecture", target=lecture,
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
            actor=actor, action=_ACTION_DIPLOMA_LECTURE_DELETED, target_type="diploma_lecture", target=lecture,
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
        _remove_scope_assignment_rows(
            ModeratorAssignment.objects.filter(cluster__cohort=cohort),
            actor=actor,
            assignment_type="moderator_assignment",
        )
        lecture_rows = list(cohort.lectures.values_list("id", "audio_storage_key"))
        lecture_keys = [row[1] for row in lecture_rows]
        prefixes = [_audio_prefix_for_lecture(row[0], diploma=True) for row in lecture_rows]
        _record_academic_delete(
            actor=actor, action=_ACTION_COHORT_DELETED, target_type="cohort", target=cohort,
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
    if video_id and video_id != lecture.youtube_video_id:
        lecture.youtube_video_id = video_id
        lecture.save(update_fields=["youtube_video_id"])


def enqueue_youtube_metadata_refresh(lecture, *, diploma: bool = False) -> None:
    """Enqueue the bounded metadata fetch for a lecture, unless already fetched.

    Called from the lecture create/edit views. A lecture with a saved duration
    does not need another fetch; an enqueue failure (broker unavailable) is
    recorded as an explicit 'metadata unavailable' attempt state instead of
    failing silently.
    """
    from apps.academics.tasks import refresh_youtube_metadata_task

    canonicalize_youtube_id(lecture)
    if lecture.youtube_duration_seconds:
        return
    if not lecture.youtube_video_id:
        return
    try:
        refresh_youtube_metadata_task.delay(lecture.id, diploma=diploma)
    except Exception as exc:  # noqa: BLE001 - broker down; persist the unavailable state
        logger.error("Could not enqueue YouTube metadata fetch for lecture %s: %s", lecture.id, exc)
        lecture.youtube_metadata_fetched_at = timezone.now()
        lecture.save(update_fields=["youtube_metadata_fetched_at"])


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


def _duration_weighted_progress(lecture_qs, completed_lecture_ids) -> dict:
    from django.db.models import Sum

    totals = lecture_qs.aggregate(total_seconds=Sum("youtube_duration_seconds"))
    total_seconds = Decimal(str(totals["total_seconds"] or 0))
    completed_qs = lecture_qs.filter(id__in=completed_lecture_ids)
    completed_seconds = Decimal(
        str(completed_qs.aggregate(completed=Sum("youtube_duration_seconds"))["completed"] or 0)
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
    """Duration-weighted video progress for a student's course enrollment.

    Callers must check ``can_access_course`` before rendering; this selector
    itself returns a zero result when no enrollment exists.
    """
    lecture_qs = course_version.lectures.filter(_progress_lecture_filter())
    enrollment = CourseEnrollment.objects.filter(
        student=student_profile, course_version=course_version,
    ).first()
    if enrollment is None:
        return _empty_progress_result()
    completed_lecture_ids = LectureWatchProgress.objects.filter(
        lecture__in=lecture_qs,
        student=student_profile,
        course_enrollment=enrollment,
        status=WATCH_STATUS_COMPLETED,
    ).values_list("lecture_id", flat=True)
    return _duration_weighted_progress(lecture_qs, completed_lecture_ids)


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


# --- Watch server ------------------------------------------------------------

DEFAULT_COMPLETION_THRESHOLD = Decimal("80.0")
DEFAULT_CHECKPOINT_INTERVAL_SECONDS = 5
def start_lecture_watch(*, student, lecture):
    """Start or resume a watch session for a lecture.
    Returns dict with session_id, resume_position, duration, interval, threshold."""
    if not lecture.course_version.course.video_tracking_enabled:
        raise ValueError(_("Video tracking is disabled for this course."))

    # Duration comes ONLY from persisted metadata; never from the client and
    # never from a remote fetch in the request path (S2-03).
    duration = lecture.youtube_duration_seconds or 0

    version = lecture.course_version
    if not can_access_course(student, version):
        raise ValueError(_("No active enrollment."))

    threshold = version.course.video_completion_percent or 80

    with transaction.atomic():
        # Lock existing row or create
        lwp = (
            LectureWatchProgress.objects
            .select_for_update()
            .filter(student=student, lecture=lecture)
            .first()
        )
        if lwp is None:
            lwp = LectureWatchProgress(
                student=student,
                lecture=lecture,
                course_enrollment=_get_course_enrollment(student, version),
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
        # A violated session must restart from zero with a fresh session (S2-04);
        # other restarts keep the validated resume position.
        resume_pos = Decimal("0") if lwp.status == WATCH_STATUS_VIOLATED else lwp.resume_position_seconds
        lwp.status = WATCH_STATUS_IN_PROGRESS
        lwp.violation_reason = VIOLATION_NONE
        lwp.last_sequence = 0
        lwp.last_position_seconds = Decimal("0")
        lwp.validated_contiguous_seconds = Decimal("0")
        lwp.last_checkpoint_at = None
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

    session_id_str = payload.get("session_id", "")
    sequence = payload.get("sequence", 0)
    position = Decimal(str(payload.get("position", 0)))
    player_state = payload.get("player_state", "")
    rate = Decimal(str(payload.get("rate", 1)))

    if not session_id_str:
        return {"error": "missing_session_id", "status": 400}

    try:
        session_id = uuid.UUID(session_id_str)
    except ValueError:
        return {"error": "invalid_session_id", "status": 400}

    if not can_access_course(student, lecture.course_version):
        return {"error": "no_access", "status": 403}

    duration = Decimal(str(lecture.youtube_duration_seconds or 1))
    duration_known = lecture.youtube_duration_seconds is not None

    version = lecture.course_version
    threshold = Decimal(str(version.course.video_completion_percent or 80))

    with transaction.atomic():
        lwp = (
            LectureWatchProgress.objects
            .select_for_update()
            .filter(student=student, lecture=lecture, active_session_id=session_id)
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
        # A violated session cannot be resumed; the client must start a new
        # session from zero (S2-04).
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


def get_visible_diploma_lectures(student_profile, enrollment, semester=None):
    """Return visible (published & released) diploma lectures for an enrollment."""
    if enrollment.status != ENROLLMENT_STATUS_ACTIVE:
        return DiplomaLecture.objects.none()
    if enrollment.expires_at and timezone.now() >= enrollment.expires_at:
        return DiplomaLecture.objects.none()
    access = get_student_diploma_access(enrollment)
    if semester is not None and semester.id not in {item.id for item in access["visible_semesters"]}:
        return DiplomaLecture.objects.none()
    qs = DiplomaLecture.objects.filter(
        cohort=enrollment.cohort,
        is_published=True,
    ).filter(
        Q(release_at__isnull=True) | Q(release_at__lte=timezone.now()),
    )
    if semester is not None:
        qs = qs.filter(semester=semester)
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

    threshold = Decimal(str(lecture.cohort.diploma.video_completion_percent or 80))

    with transaction.atomic():
        lwp = (
            DiplomaLectureWatchProgress.objects
            .select_for_update()
            .filter(student=student, lecture=lecture)
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
        # A violated session must restart from zero with a fresh session (S2-04).
        resume_pos = Decimal("0") if lwp.status == WATCH_STATUS_VIOLATED else lwp.resume_position_seconds
        lwp.status = WATCH_STATUS_IN_PROGRESS
        lwp.violation_reason = VIOLATION_NONE
        lwp.last_sequence = 0
        lwp.last_position_seconds = Decimal("0")
        lwp.validated_contiguous_seconds = Decimal("0")
        lwp.last_checkpoint_at = None
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

    session_id_str = payload.get("session_id", "")
    sequence = payload.get("sequence", 0)
    position = Decimal(str(payload.get("position", 0)))
    player_state = payload.get("player_state", "")
    rate = Decimal(str(payload.get("rate", 1)))

    if not session_id_str:
        return {"error": "missing_session_id", "status": 400}

    try:
        session_id = uuid.UUID(session_id_str)
    except ValueError:
        return {"error": "invalid_session_id", "status": 400}

    if not can_access_diploma_lecture(student, enrollment, lecture):
        return {"error": "no_access", "status": 403}

    duration = Decimal(str(lecture.youtube_duration_seconds or 1))
    duration_known = lecture.youtube_duration_seconds is not None

    threshold = Decimal(str(lecture.cohort.diploma.video_completion_percent or 80))

    with transaction.atomic():
        lwp = (
            DiplomaLectureWatchProgress.objects
            .select_for_update()
            .filter(student=student, lecture=lecture, active_session_id=session_id)
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
