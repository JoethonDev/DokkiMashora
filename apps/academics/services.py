"""Access policy and enrollment business logic for academics."""

import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import requests
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.academics.constants import (
    ENROLLMENT_STATUS_ACTIVE,
    ENROLLMENT_STATUS_EXPIRED,
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
    ModeratorStudentAssignment,
    TeacherAssignment,
)
from apps.accounts.models import User
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
from apps.progression.models import SemesterGradeComponent, StudentSemesterProgress


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
    """Return the versions the student is enrolled in, including archived ones."""
    return CourseVersion.objects.filter(
        enrollments__student=student_profile,
        enrollments__status=ENROLLMENT_STATUS_ACTIVE,
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
    enrollment, _ = CourseEnrollment.objects.get_or_create(
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
    enrollment, _ = DiplomaEnrollment.objects.get_or_create(
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
       whole_diploma     → diploma_id
      diploma_semester  → semester_id
      diploma_cohort    → cohort_id
      semester_cohort   → semester_id + cohort_id
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
    """Return all CourseVersions a teacher has access to via their assignments."""
    if not user or not user.is_authenticated:
        return []
    qs = TeacherAssignment.objects.filter(teacher=user, active=True)
    versions = CourseVersion.objects.none()
    for a in qs:
        if a.scope_type == SCOPE_WHOLE_DIPLOMA:
            versions = versions | CourseVersion.objects.filter(cohort__diploma=a.diploma)
        elif a.scope_type == SCOPE_DIPLOMA_SEMESTER:
            versions = versions | CourseVersion.objects.filter(semester=a.semester)
        elif a.scope_type == SCOPE_DIPLOMA_COHORT:
            versions = versions | CourseVersion.objects.filter(cohort=a.cohort)
        elif a.scope_type == SCOPE_SEMESTER_COHORT:
            versions = versions | CourseVersion.objects.filter(semester=a.semester, cohort=a.cohort)
    return versions.distinct()


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
    """Create a cohort. Delegates to create_cohort_with_defaults — no CourseVersion rows created."""
    return create_cohort_with_defaults(diploma=diploma, form_data=form_data, actor=actor)


# --- Deletion services -------------------------------------------------------

def delete_diploma_course(*, course):
    """Hard-delete a diploma course and all owned academic content."""
    with transaction.atomic():
        course.delete()


def delete_cohort_delivery(*, cohort):
    """Hard-delete a cohort's delivery: enrollments, versions/content, clusters, scoped assignments/exams."""
    with transaction.atomic():
        cohort.delete()


# --- YouTube metadata --------------------------------------------------------

_YT_VIDEO_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?.*?v=|embed/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)
_YT_PAGE_TIMEOUT = 10


def extract_youtube_video_id(url: str) -> str:
    """Extract the 11-character video ID from a YouTube URL, or return empty string."""
    m = _YT_VIDEO_ID_RE.search(url)
    return m.group(1) if m else ""


def fetch_youtube_duration_seconds(video_id: str) -> int | None:
    """Fetch duration from YouTube watch page. Returns seconds or None on failure."""
    if not video_id:
        return None
    try:
        resp = requests.get(
            f"https://www.youtube.com/watch?v={video_id}",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=_YT_PAGE_TIMEOUT,
        )
        resp.raise_for_status()
        m = re.search(r'"lengthSeconds":"(\d+)"', resp.text)
        return int(m.group(1)) if m else None
    except Exception:  # noqa: BLE001
        return None


def populate_youtube_metadata(*, lecture) -> None:
    """Extract video_id from youtube_url, fetch metadata, update fields."""
    video_id = extract_youtube_video_id(lecture.youtube_url) if lecture.youtube_url else ""
    changed = False
    if video_id != lecture.youtube_video_id:
        lecture.youtube_video_id = video_id
        changed = True
    # Fetch duration if we have a video_id but no duration yet or URL changed
    if video_id and (not lecture.youtube_duration_seconds or changed):
        duration = fetch_youtube_duration_seconds(video_id)
        if duration is not None:
            lecture.youtube_duration_seconds = duration
            changed = True
    if changed:
        lecture.youtube_metadata_fetched_at = timezone.now()
        lecture.save(update_fields=[
            "youtube_video_id",
            "youtube_duration_seconds",
            "youtube_metadata_fetched_at",
        ])


# --- Watch server ------------------------------------------------------------

DEFAULT_COMPLETION_THRESHOLD = Decimal("80.0")
DEFAULT_CHECKPOINT_INTERVAL_SECONDS = 5


def start_lecture_watch(*, student, lecture):
    """Start or resume a watch session for a lecture.
    Returns dict with session_id, resume_position, duration, interval, threshold."""
    if not lecture.course_version.course.video_tracking_enabled:
        raise ValueError(_("Video tracking is disabled for this course."))

    duration = lecture.youtube_duration_seconds
    if not duration and lecture.youtube_url:
        try:
            populate_youtube_metadata(lecture=lecture)
        except Exception:  # noqa: BLE001
            pass
        duration = lecture.youtube_duration_seconds or 0

    version = lecture.course_version
    enrollment = _get_course_enrollment(student, version)
    if enrollment is None:
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
        # Reset violated/in-progress — but preserve resume position
        resume_pos = lwp.resume_position_seconds
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


def record_lecture_checkpoint(*, student, lecture, payload, now=None):
    """Record a heartbeat checkpoint. Returns dict with progress, status, violation.
    payload must contain: session_id, sequence, position, player_state, rate."""
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

    duration = lecture.youtube_duration_seconds or Decimal(str(payload.get("duration", 0)))
    if not duration:
        duration = Decimal("1")  # prevent division by zero

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
                "progress": float(min(position / duration * 100, 100)),
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
                "progress": float(min(position / duration * 100, 100)),
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
                "progress": float(min(position / duration * 100, 100)),
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
                "progress": float(min(position / duration * 100, 100)),
            }

        # Advance validated seconds (only for forward movement within video)
        if position > lwp.last_position_seconds and position <= Decimal(str(duration)):
            advance = min(position - lwp.last_position_seconds, elapsed + Decimal("2"))
            lwp.validated_contiguous_seconds += advance

        lwp.last_sequence = sequence
        lwp.last_position_seconds = position
        lwp.last_player_state = player_state
        lwp.last_checkpoint_at = now
        lwp.resume_position_seconds = position

        # Progress based on actual position, not validated seconds
        progress_pct = min(position / Decimal(str(duration)) * 100, Decimal("100"))
        completed = False
        if progress_pct >= threshold and lwp.status != WATCH_STATUS_COMPLETED:
            lwp.status = WATCH_STATUS_COMPLETED
            lwp.completed_at = now
            completed = True

        lwp.save()

    return {
        "progress": float(progress_pct),
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

    duration = lecture.youtube_duration_seconds
    if not duration and lecture.youtube_url:
        try:
            populate_youtube_metadata(lecture=lecture)
        except Exception:
            pass
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
        resume_pos = lwp.resume_position_seconds
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
    """Record a heartbeat checkpoint for a diploma lecture."""
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

    duration = lecture.youtube_duration_seconds or Decimal(str(payload.get("duration", 0)))
    if not duration:
        duration = Decimal("1")

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
                "progress": float(min(position / duration * 100, 100)),
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
                "progress": float(min(position / duration * 100, 100)),
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
                "progress": float(min(position / duration * 100, 100)),
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
                "progress": float(min(position / duration * 100, 100)),
            }

        # Advance validated seconds
        if position > lwp.last_position_seconds and position <= Decimal(str(duration)):
            advance = min(position - lwp.last_position_seconds, elapsed + Decimal("2"))
            lwp.validated_contiguous_seconds += advance

        lwp.last_sequence = sequence
        lwp.last_position_seconds = position
        lwp.last_player_state = player_state
        lwp.last_checkpoint_at = now
        lwp.resume_position_seconds = position

        progress_pct = min(position / Decimal(str(duration)) * 100, Decimal("100"))
        completed = False
        if progress_pct >= threshold and lwp.status != WATCH_STATUS_COMPLETED:
            lwp.status = WATCH_STATUS_COMPLETED
            lwp.completed_at = now
            completed = True

        lwp.save()

    return {
        "progress": float(progress_pct),
        "status": lwp.status,
        "violation": lwp.violation_reason,
        "resume_position": float(lwp.resume_position_seconds),
        "completed": completed,
    }
