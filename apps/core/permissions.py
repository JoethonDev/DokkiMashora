"""Permission helpers and decorators used across the LMS."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE, SCOPE_COURSE_VERSION, SCOPE_DIPLOMA_COHORT
from apps.academics.models import (
    DiplomaEnrollment,
    ModeratorAssignment,
    ModeratorStudentAssignment,
    TeacherAssignment,
)
from apps.accounts.constants import (
    GROUP_ADMIN,
    GROUP_MODERATOR,
    GROUP_REVIEWER,
    GROUP_SECRETARY,
    GROUP_STUDENT,
    GROUP_SUPER_ADMIN,
    GROUP_TEACHER,
)


def safe_int(value: str | None, default: int | None = None) -> int | None:
    """Parse an integer from a string, returning *default* on failure."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def safe_date_str(value: str | None) -> str | None:
    """Validate a YYYY-MM-DD date string, returning None if invalid."""
    if not value:
        return None
    try:
        datetime.strptime(value.strip(), "%Y-%m-%d")
        return value.strip()
    except (ValueError, TypeError):
        return None


def validate_score(value, max_score=None):
    """Convert and validate a score. Returns Decimal or raises ValueError."""
    try:
        score = Decimal(str(value))
    except Exception as exc:
        raise ValueError(_("Invalid score value.")) from exc
    if not score.is_finite():
        raise ValueError(_("Score must be a finite number."))
    if score < 0:
        raise ValueError(_("Score cannot be negative."))
    if max_score is not None and score > Decimal(str(max_score)):
        raise ValueError(_("Score cannot exceed %(max)s.") % {"max": max_score})
    return score


def _user_in_any_group(user, group_names: tuple[str, ...]) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=group_names).exists()


ROLE_EXAM_STAFF = (GROUP_SUPER_ADMIN, GROUP_ADMIN)
ROLE_SENIOR_STAFF = (GROUP_SUPER_ADMIN, GROUP_ADMIN)
ROLE_REVIEW_STAFF = (GROUP_SUPER_ADMIN, GROUP_ADMIN, GROUP_REVIEWER)
ROLE_ATTENDANCE_STAFF = (GROUP_SUPER_ADMIN, GROUP_ADMIN, GROUP_SECRETARY)
ROLE_GRADING_STAFF = (GROUP_SUPER_ADMIN, GROUP_ADMIN, GROUP_MODERATOR)


def is_super_admin(user) -> bool:
    return bool(
        user
        and user.is_authenticated
        and (user.is_superuser or _user_in_any_group(user, (GROUP_SUPER_ADMIN,)))
    )


def is_admin(user) -> bool:
    return bool(user and user.is_authenticated and _user_in_any_group(user, (GROUP_SUPER_ADMIN, GROUP_ADMIN)))


def is_moderator(user) -> bool:
    return bool(
        user
        and user.is_authenticated
        and _user_in_any_group(user, (GROUP_SUPER_ADMIN, GROUP_ADMIN, GROUP_MODERATOR))
    )


def is_reviewer(user) -> bool:
    return bool(
        user
        and user.is_authenticated
        and _user_in_any_group(user, ROLE_REVIEW_STAFF)
    )


def is_secretary(user) -> bool:
    return bool(
        user
        and user.is_authenticated
        and _user_in_any_group(user, ROLE_ATTENDANCE_STAFF)
    )


def is_teacher(user) -> bool:
    return bool(
        user
        and user.is_authenticated
        and _user_in_any_group(user, (GROUP_SUPER_ADMIN, GROUP_ADMIN, GROUP_TEACHER))
    )


def is_student(user) -> bool:
    return bool(user and user.is_authenticated and _user_in_any_group(user, (GROUP_STUDENT,)))


def is_teacher_of_cohort(user, cohort) -> bool:
    if not user or not user.is_authenticated or cohort is None:
        return False
    if is_admin(user):
        return True
    return TeacherAssignment.objects.filter(
        teacher=user,
        active=True,
    ).filter(
        scope_type=SCOPE_DIPLOMA_COHORT, cohort=cohort,
    ).exists()


def is_moderator_for_student(user, student, cohort=None) -> bool:
    if not user or not user.is_authenticated or student is None:
        return False
    if is_admin(user):
        return True
    student_profile = _student_profile(student)
    if student_profile is None:
        return False
    assignments = ModeratorStudentAssignment.objects.filter(
        moderator=user,
        student=student_profile,
        active=True,
    )
    if cohort is not None:
        assignments = assignments.filter(cohort=cohort)
    if assignments.exists():
        return True
    mod_qs = ModeratorAssignment.objects.filter(moderator=user, active=True)
    if cohort is not None:
        mod_qs = mod_qs.filter(cohort=cohort)
    return mod_qs.filter(
        Q(cluster__memberships__student=student_profile)
        | (Q(cluster__isnull=True) & Q(cohort__enrollments__student=student_profile))
    ).exists()


def can_grade_attempt(user, attempt) -> bool:
    if not user or not user.is_authenticated or attempt is None:
        return False
    if is_admin(user):
        return True
    cohort = _cohort_for_staff_object(attempt)
    student = _student_for_staff_object(attempt)
    return is_teacher_of_cohort(user, cohort) or is_moderator_for_student(user, student, cohort)


def can_view_student_progress(user, student, cohort=None) -> bool:
    if not user or not user.is_authenticated or student is None:
        return False
    if is_admin(user):
        return True
    student_profile = _student_profile(student)
    if student_profile is None:
        return False
    if student_profile.user_id == user.id:
        return True
    if is_moderator_for_student(user, student_profile, cohort):
        return True
    if cohort is not None:
        return is_teacher_of_cohort(user, cohort)
    assigned_cohorts = TeacherAssignment.objects.filter(
        teacher=user,
        scope_type=SCOPE_DIPLOMA_COHORT,
        active=True,
    ).values("cohort_id")
    return DiplomaEnrollment.objects.filter(
        student=student_profile,
        status=ENROLLMENT_STATUS_ACTIVE,
        cohort_id__in=assigned_cohorts,
    ).exists()


def assessment_staff_scope_filter(user, prefix: str = "") -> Q:
    if not user or not user.is_authenticated:
        return Q(pk__isnull=True)
    if is_admin(user):
        return Q()
    filter_prefix = f"{prefix}__" if prefix else ""
    teacher_cohorts = TeacherAssignment.objects.filter(
        teacher=user, scope_type=SCOPE_DIPLOMA_COHORT, active=True,
    ).values("cohort_id")
    teacher_cv_ids = TeacherAssignment.objects.filter(
        teacher=user, scope_type=SCOPE_COURSE_VERSION, active=True,
        course_version_id__isnull=False,
    ).values("course_version_id")
    teacher_filter = (
        Q(**{f"{filter_prefix}assessment__cohort_id__in": teacher_cohorts})
        | Q(**{f"{filter_prefix}assessment__course_version_id__in": teacher_cv_ids})
    )
    moderator_filter = (
        Q(**{f"{filter_prefix}student__student_profile__moderator_assignments__moderator": user})
        & Q(**{f"{filter_prefix}student__student_profile__moderator_assignments__active": True})
        & Q(**{f"{filter_prefix}assessment__cohort_id": F(f"{filter_prefix}student__student_profile__moderator_assignments__cohort_id")})
    )
    mod_wide_cohorts = list(ModeratorAssignment.objects.filter(
        moderator=user, active=True, cluster__isnull=True,
    ).values_list("cohort_id", flat=True))
    mod_wide_cvs = list(ModeratorAssignment.objects.filter(
        moderator=user, active=True, cluster__isnull=True, course_version_id__isnull=False,
    ).values_list("course_version_id", flat=True))
    mod_cluster_students = list(ModeratorAssignment.objects.filter(
        moderator=user, active=True, cluster__isnull=False,
    ).values_list("cluster__memberships__student_id", flat=True))
    mod_cluster_cohorts = list(ModeratorAssignment.objects.filter(
        moderator=user, active=True, cluster__isnull=False,
    ).values_list("cohort_id", flat=True))
    mod_filter = Q()
    if mod_wide_cohorts:
        mod_filter |= Q(**{f"{filter_prefix}assessment__cohort_id__in": mod_wide_cohorts})
    if mod_wide_cvs:
        mod_filter |= Q(**{f"{filter_prefix}assessment__course_version_id__in": mod_wide_cvs})
    if mod_cluster_students:
        mod_filter |= (
            Q(**{f"{filter_prefix}student__student_profile_id__in": mod_cluster_students})
            & Q(**{f"{filter_prefix}assessment__cohort_id__in": mod_cluster_cohorts})
        )
    return teacher_filter | moderator_filter | mod_filter


def _student_profile(student):
    if hasattr(student, "user_id"):
        return student
    return getattr(student, "student_profile", None)


def _student_for_staff_object(obj):
    if hasattr(obj, "student"):
        return obj.student
    if hasattr(obj, "attempt") and hasattr(obj.attempt, "student"):
        return obj.attempt.student
    return None


def _cohort_for_staff_object(obj):
    exam = getattr(obj, "exam", None)
    if exam is not None:
        return getattr(exam, "cohort", None)
    assessment = getattr(obj, "assessment", None)
    if assessment is None and hasattr(obj, "attempt"):
        assessment = getattr(obj.attempt, "assessment", None)
    if assessment is None:
        return None
    if assessment.cohort_id:
        return assessment.cohort
    return None


def assessment_scope_filter(user) -> Q:
    """Return a Q filter for Assessment querysets scoped to a non-admin staff user."""
    if not user or not user.is_authenticated:
        return Q(pk__isnull=True)
    if is_admin(user):
        return Q()
    teacher_cohorts = TeacherAssignment.objects.filter(
        teacher=user, scope_type=SCOPE_DIPLOMA_COHORT, active=True,
    ).values("cohort_id")
    teacher_cvs = TeacherAssignment.objects.filter(
        teacher=user, scope_type=SCOPE_COURSE_VERSION, active=True,
    ).values("course_version_id")
    mod_cohorts = ModeratorAssignment.objects.filter(
        moderator=user, active=True,
    ).values("cohort_id")
    mod_cvs = ModeratorAssignment.objects.filter(
        moderator=user, active=True,
    ).values("course_version_id")
    return (
        Q(cohort_id__in=teacher_cohorts)
        | Q(course_version_id__in=teacher_cvs)
        | Q(cohort_id__in=mod_cohorts)
        | Q(course_version_id__in=mod_cvs)
    )


def enrollment_scope_filter(user, enrollment_model: str = "course") -> Q:
    """Return a Q filter for CourseEnrollment or DiplomaEnrollment querysets."""  # noqa: E501
    if not user or not user.is_authenticated:
        return Q(pk__isnull=True)
    if is_admin(user):
        return Q()
    teacher_cohorts = TeacherAssignment.objects.filter(
        teacher=user, scope_type=SCOPE_DIPLOMA_COHORT, active=True,
    ).values("cohort_id")
    teacher_cvs = TeacherAssignment.objects.filter(
        teacher=user, scope_type=SCOPE_COURSE_VERSION, active=True,
    ).values("course_version_id")
    mod_cohorts = ModeratorAssignment.objects.filter(
        moderator=user, active=True,
    ).values("cohort_id")
    mod_cvs = ModeratorAssignment.objects.filter(
        moderator=user, active=True,
    ).values("course_version_id")
    if enrollment_model == "course":
        return (
            Q(course_version_id__in=teacher_cvs)
            | Q(course_version_id__in=mod_cvs)
        )
    return (
        Q(cohort_id__in=teacher_cohorts)
        | Q(cohort_id__in=mod_cohorts)
    )


def can_manage_course_version(user, course_version) -> bool:
    """Check if a non-admin staff user can manage lectures on a course version."""
    if not user or not user.is_authenticated:
        return False
    if is_admin(user):
        return True
    if TeacherAssignment.objects.filter(
        teacher=user, scope_type=SCOPE_COURSE_VERSION, course_version=course_version, active=True,
    ).exists():
        return True
    if ModeratorAssignment.objects.filter(
        moderator=user, course_version=course_version, active=True,
    ).exists():
        return True
    return False


def staff_required(view_func: Callable) -> Callable:
    """Allow only staff (admins/moderators/teachers/superusers)."""

    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not (request.user.is_staff or is_moderator(request.user) or is_teacher(request.user)):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return _wrapped


def role_required(*group_names: str) -> Callable:
    """Restrict a view to users in any of the given group names."""

    allowed = tuple(group_names)

    def decorator(view_func: Callable) -> Callable:
        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):
            if not _user_in_any_group(request.user, allowed):
                raise PermissionDenied
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


def student_required(view_func: Callable) -> Callable:
    """Allow student portal pages to students and intentional superuser previews."""

    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not (is_student(request.user) or request.user.is_superuser):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return _wrapped
