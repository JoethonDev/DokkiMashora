"""Permission helpers and decorators used across the LMS.

Active role/action matrix (P1-01)
---------------------------------
Super Admin / Admin : platform administration; general assessment grading;
                      manual Diploma grade release; progression; payments;
                      certificates.
Moderator           : assigned-cluster Diploma Homework grading only; never
                      releases grades.
Reviewer            : assigned anonymous Semester Exam grading only; never
                      releases grades.
Secretary           : Attendance only.
Student             : own workflows only.
Teacher             : DEFERRED / not a current product role. Preserved as
                      historical data only; no active workflow decision in
                      this module grants Teacher capability.

Every helper below must be combined with the scoped querysets/mutations a
domain already applies. A role membership check alone is never sufficient.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import (
    DiplomaEnrollment,
    ModeratorAssignment,
    ModeratorStudentAssignment,
)
from apps.accounts.constants import (
    GROUP_ADMIN,
    GROUP_MODERATOR,
    GROUP_REVIEWER,
    GROUP_SECRETARY,
    GROUP_STUDENT,
    GROUP_SUPER_ADMIN,
)
from apps.assessments.constants import ASSESSMENT_TYPE_HOMEWORK


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
    # Cache all group names per-request to avoid N queries (Take path did 5 group_exists before)
    if hasattr(user, "_group_names_cache"):
        cached = user._group_names_cache
    else:
        try:
            cached = set(user.groups.values_list("name", flat=True))
        except Exception:
            cached = set()
        user._group_names_cache = cached
    return any(name in cached for name in group_names)


ROLE_EXAM_STAFF = (GROUP_SUPER_ADMIN, GROUP_ADMIN)
ROLE_SENIOR_STAFF = (GROUP_SUPER_ADMIN, GROUP_ADMIN)
ROLE_REVIEW_STAFF = (GROUP_SUPER_ADMIN, GROUP_ADMIN, GROUP_REVIEWER)
ROLE_ATTENDANCE_STAFF = (GROUP_SUPER_ADMIN, GROUP_ADMIN, GROUP_SECRETARY)
ROLE_HOMEWORK_GRADING_STAFF = (GROUP_SUPER_ADMIN, GROUP_ADMIN, GROUP_MODERATOR)
ROLE_REVIEWER = (GROUP_SUPER_ADMIN, GROUP_ADMIN, GROUP_REVIEWER)


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


def is_student(user) -> bool:
    return bool(user and user.is_authenticated and _user_in_any_group(user, (GROUP_STUDENT,)))


def is_moderator_for_student(user, student, cohort=None) -> bool:
    if not user or not user.is_authenticated or student is None:
        return False
    if is_admin(user):
        return True
    # A missing cohort must never widen into an unrestricted match (S2-14).
    # Moderators are scoped to explicit assignments; cohort-less callers must
    # pass the cohort they derived from the object being authorized.
    if cohort is None:
        return False
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
    # Cluster or wide-cohort coverage requires an active enrollment in the
    # cohort; completed/expired/pending/restricted students stay out of scope.
    return mod_qs.filter(
        cohort__enrollments__student=student_profile,
        cohort__enrollments__status=ENROLLMENT_STATUS_ACTIVE,
    ).filter(
        Q(cluster__memberships__student=student_profile) | Q(cluster__isnull=True)
    ).exists()


def can_moderator_grade_homework(user, submission) -> bool:
    """Authorize grading of one Homework submission.

    Admins/Super Admins always qualify. A Moderator qualifies only when the
    submission's assessment is a diploma-scoped Homework (Diploma/Cohort/
    Semester, not a course version) and the submitting student is inside the
    Moderator's active assigned scope for that cohort. Other roles never
    receive Homework grading through this helper, and Moderators are never
    authorized for other assessment types here.
    """
    if not user or not user.is_authenticated or submission is None:
        return False
    if is_admin(user):
        return True
    if not _user_in_any_group(user, (GROUP_MODERATOR,)):
        return False
    assessment = getattr(submission, "assessment", None)
    if assessment is None:
        return False
    if assessment.assessment_type != ASSESSMENT_TYPE_HOMEWORK:
        return False
    if not (assessment.course_version_id is None and assessment.cohort_id and assessment.semester_id):
        return False
    student_profile = _student_profile(getattr(submission, "student", None))
    if student_profile is None:
        return False
    return is_moderator_for_student(user, student_profile, assessment.cohort)


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
    return is_moderator_for_student(user, student_profile, cohort)


def assessment_staff_scope_filter(user, prefix: str = "") -> Q:
    if not user or not user.is_authenticated:
        return Q(pk__isnull=True)
    if is_admin(user):
        return Q()
    filter_prefix = f"{prefix}__" if prefix else ""
    # Only Moderators may reach this filter now. A Moderator without any
    # active assignment must never see an unscoped match-all result.
    has_moderator_scope = (
        ModeratorStudentAssignment.objects.filter(moderator=user, active=True).exists()
        or ModeratorAssignment.objects.filter(moderator=user, active=True).exists()
    )
    if not has_moderator_scope:
        return Q(pk__isnull=True)
    moderator_filter = (
        Q(**{f"{filter_prefix}student__student_profile__moderator_assignments__moderator": user})
        & Q(**{f"{filter_prefix}student__student_profile__moderator_assignments__active": True})
        & Q(**{f"{filter_prefix}assessment__cohort_id": F(f"{filter_prefix}student__student_profile__moderator_assignments__cohort_id")})
    )
    mod_wide_cohorts = list(ModeratorAssignment.objects.filter(
        moderator=user, active=True, cluster__isnull=True,
    ).values_list("cohort_id", flat=True))
    mod_cluster_students = list(ModeratorAssignment.objects.filter(
        moderator=user, active=True, cluster__isnull=False,
    ).values_list("cluster__memberships__student_id", flat=True))
    mod_cluster_cohorts = list(ModeratorAssignment.objects.filter(
        moderator=user, active=True, cluster__isnull=False,
    ).values_list("cohort_id", flat=True))
    # Cluster coverage applies only to students with an active enrollment in
    # the cluster's cohort (S2-15).
    active_cluster_students = list(DiplomaEnrollment.objects.filter(
        student_id__in=mod_cluster_students,
        cohort_id__in=mod_cluster_cohorts,
        status=ENROLLMENT_STATUS_ACTIVE,
    ).values_list("student_id", flat=True))
    mod_filter = Q()
    if mod_wide_cohorts:
        mod_filter |= Q(**{f"{filter_prefix}assessment__cohort_id__in": mod_wide_cohorts})
    if active_cluster_students:
        mod_filter |= (
            Q(**{f"{filter_prefix}student__student_profile_id__in": active_cluster_students})
            & Q(**{f"{filter_prefix}assessment__cohort_id__in": mod_cluster_cohorts})
        )
    return moderator_filter | mod_filter


def _student_profile(student):
    if hasattr(student, "user_id"):
        return student
    return getattr(student, "student_profile", None)


def assessment_scope_filter(user) -> Q:
    """Return a Q filter for Assessment querysets scoped to a non-admin staff user.

    Moderator scope is Diploma-only (D-001): a Moderator sees assessments for
    their assigned cohorts; course-version scope is intentionally excluded.
    """
    if not user or not user.is_authenticated:
        return Q(pk__isnull=True)
    if is_admin(user):
        return Q()
    mod_cohorts = ModeratorAssignment.objects.filter(
        moderator=user, active=True,
    ).values("cohort_id")
    return Q(cohort_id__in=mod_cohorts)


def enrollment_scope_filter(user, enrollment_model: str = "course") -> Q:
    """Return a Q filter for CourseEnrollment or DiplomaEnrollment querysets.

    Moderator scope is Diploma-only (D-001): moderators never see course
    enrollments; they are scoped to Diploma cohorts only.
    """  # noqa: E501
    if not user or not user.is_authenticated:
        return Q(pk__isnull=True)
    if is_admin(user):
        return Q()
    mod_cohorts = ModeratorAssignment.objects.filter(
        moderator=user, active=True,
    ).values("cohort_id")
    if enrollment_model == "course":
        return Q(pk__isnull=True)
    return Q(cohort_id__in=mod_cohorts)


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
