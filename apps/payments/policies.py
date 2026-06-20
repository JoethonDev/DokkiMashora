from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model

from apps.accounts.constants import GROUP_ADMIN, GROUP_MODERATOR, GROUP_SUPER_ADMIN

User = get_user_model()


def can_upload_payments(user: User) -> bool:
    if not user.is_authenticated:
        return False
    return user.groups.filter(
        name__in=[GROUP_SUPER_ADMIN, GROUP_ADMIN, GROUP_MODERATOR]
    ).exists()


def can_view_payment_uploads(user: User) -> bool:
    if not user.is_authenticated:
        return False
    return user.groups.filter(
        name__in=[GROUP_SUPER_ADMIN, GROUP_ADMIN, GROUP_MODERATOR]
    ).exists()


def can_view_payment_records(user: User) -> bool:
    if not user.is_authenticated:
        return False
    return user.groups.filter(
        name__in=[GROUP_SUPER_ADMIN, GROUP_ADMIN, GROUP_MODERATOR]
    ).exists()


def can_view_student_payments(student_user: Any, request_user: User) -> bool:
    if not request_user.is_authenticated:
        return False
    if request_user == student_user:
        return True
    return can_view_payment_records(request_user)
