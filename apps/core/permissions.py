"""Permission helpers and decorators used across the LMS."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import F, Q

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE, SCOPE_DIPLOMA_COHORT
from apps.academics.models import DiplomaEnrollment, ModeratorStudentAssignment, TeacherAssignment
from apps.accounts.constants import (
    GROUP_ADMIN,
    GROUP_MODERATOR,
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


def _user_in_any_group(user, group_names: tuple[str, ...]) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=group_names).exists()


ROLE_EXAM_STAFF = (GROUP_SUPER_ADMIN, GROUP_ADMIN, GROUP_MODERATOR, GROUP_TEACHER)
ROLE_SENIOR_STAFF = (GROUP_SUPER_ADMIN, GROUP_ADMIN)


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
        scope_type=SCOPE_DIPLOMA_COHORT,
        cohort=cohort,
        active=True,
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
    return assignments.exists()


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
    teacher_cohorts = TeacherAssignment.objects.filter(
        teacher=user,
        scope_type=SCOPE_DIPLOMA_COHORT,
        active=True,
    ).values("cohort_id")
    filter_prefix = f"{prefix}__" if prefix else ""
    teacher_filter = (
        Q(**{f"{filter_prefix}assessment__cohort_id__in": teacher_cohorts})
        | Q(**{f"{filter_prefix}assessment__course_version__cohort_id__in": teacher_cohorts})
    )
    moderator_filter = (
        Q(**{f"{filter_prefix}student__student_profile__moderator_assignments__moderator": user})
        & Q(**{f"{filter_prefix}student__student_profile__moderator_assignments__active": True})
        & (
            Q(
                **{
                    f"{filter_prefix}assessment__cohort_id": F(
                        f"{filter_prefix}student__student_profile__moderator_assignments__cohort_id"
                    )
                }
            )
            | Q(
                **{
                    f"{filter_prefix}assessment__course_version__cohort_id": F(
                        f"{filter_prefix}student__student_profile__moderator_assignments__cohort_id"
                    )
                }
            )
        )
    )
    return teacher_filter | moderator_filter


def entrance_staff_scope_filter(user) -> Q:
    if not user or not user.is_authenticated:
        return Q(pk__isnull=True)
    if is_admin(user):
        return Q()
    teacher_cohorts = TeacherAssignment.objects.filter(
        teacher=user,
        scope_type=SCOPE_DIPLOMA_COHORT,
        active=True,
    ).values("cohort_id")
    return (
        Q(exam__cohort_id__in=teacher_cohorts)
        | (
            Q(student__moderator_assignments__moderator=user)
            & Q(student__moderator_assignments__active=True)
            & Q(exam__cohort_id=F("student__moderator_assignments__cohort_id"))
        )
    )


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
    course_version = assessment.course_version
    if course_version and course_version.cohort_id:
        return course_version.cohort
    return None


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
