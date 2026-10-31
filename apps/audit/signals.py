"""Bridge Django Admin changes into the product audit trail.

Django Admin writes ``LogEntry`` rows but bypasses the named business services.
The bridge makes those direct model edits visible to IT without changing the
Django Admin permission model.
"""

from __future__ import annotations

import logging

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import post_save
from django.dispatch import receiver

from .constants import (
    ACTION_ADMIN_CHANGE,
    ACTION_SECURITY_LOGIN,
    ACTION_SECURITY_LOGIN_FAILED,
    ACTION_SECURITY_LOGOUT,
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    SECTION_SECURITY,
    SOURCE_USER,
)
from .labels import admin_section_for_app_label
from .services import record_audit_event

logger = logging.getLogger(__name__)
User = get_user_model()


@receiver(post_save, sender=LogEntry)
def audit_admin_log_entry(sender, instance, created, **kwargs) -> None:
    if not created or instance.user_id is None:
        return
    content_type = instance.content_type
    app_label = getattr(content_type, "app_label", "") if content_type is not None else ""
    model = getattr(content_type, "model", "") if content_type is not None else ""
    try:
        record_audit_event(
            instance.user,
            ACTION_ADMIN_CHANGE,
            target_type=model or "admin",
            target_id=instance.object_id or "",
            section=admin_section_for_app_label(app_label),
            target_label=instance.object_repr or "",
            metadata={
                "change_message": instance.get_change_message(),
                "action_flag": instance.get_action_flag_display(),
                "app_label": app_label,
                "model": model,
            },
            source=SOURCE_USER,
        )
    except Exception:  # pragma: no cover - an audit failure must not break admin
        logger.exception("Could not bridge Django Admin LogEntry %s into audit", instance.pk)


@receiver(user_logged_in)
def audit_staff_login(sender, request, user, **kwargs) -> None:
    if not getattr(user, "is_staff", False):
        return
    try:
        record_audit_event(
            user,
            ACTION_SECURITY_LOGIN,
            target_type="user",
            target_id=user.pk,
            section=SECTION_SECURITY,
            target_label=str(user),
            subject_type="user",
            subject_id=user.pk,
            subject_label=str(user),
            outcome=OUTCOME_SUCCESS,
            source=SOURCE_USER,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not record staff login for %s", getattr(user, "pk", None))


@receiver(user_logged_out)
def audit_staff_logout(sender, request, user, **kwargs) -> None:
    if user is None or not getattr(user, "is_staff", False):
        return
    try:
        record_audit_event(
            user,
            ACTION_SECURITY_LOGOUT,
            target_type="user",
            target_id=user.pk,
            section=SECTION_SECURITY,
            target_label=str(user),
            subject_type="user",
            subject_id=user.pk,
            subject_label=str(user),
            outcome=OUTCOME_SUCCESS,
            source=SOURCE_USER,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not record staff logout for %s", getattr(user, "pk", None))


@receiver(user_login_failed)
def audit_staff_login_failed(sender, credentials, request=None, **kwargs) -> None:
    username = str((credentials or {}).get("username") or (credentials or {}).get("email") or "").strip()
    if not username:
        return
    try:
        user = User.objects.filter(email__iexact=username, is_staff=True).first()
        if user is None:
            return
        record_audit_event(
            None,
            ACTION_SECURITY_LOGIN_FAILED,
            target_type="user",
            target_id=user.pk,
            section=SECTION_SECURITY,
            target_label=str(user),
            subject_type="user",
            subject_id=user.pk,
            subject_label=str(user),
            metadata={"email": username},
            outcome=OUTCOME_FAILURE,
            source=SOURCE_USER,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not record failed staff login for %s", username)
