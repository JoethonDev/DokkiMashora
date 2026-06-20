"""Permission helpers and decorators used across the LMS."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

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
    from datetime import datetime
    try:
        datetime.strptime(value.strip(), "%Y-%m-%d")
        return value.strip()
    except (ValueError, TypeError):
        return None


def _user_in_any_group(user, group_names: tuple[str, ...]) -> bool:
    if not user.is_authenticated:
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
