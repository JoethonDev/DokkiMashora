"""SMTP configuration helpers and probe actions."""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.core.mail.backends.smtp import EmailBackend as SMTPEmailBackend
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import EmailConfiguration

logger = logging.getLogger("apps.communications.email_config")


def get_active_config() -> EmailConfiguration | None:
    return EmailConfiguration.get_active()


def build_backend(config: EmailConfiguration | None = None) -> SMTPEmailBackend:
    config = config or get_active_config()
    if config is None:
        return SMTPEmailBackend(
            host=settings.EMAIL_HOST,
            port=settings.EMAIL_PORT,
            username=settings.EMAIL_HOST_USER,
            password=settings.EMAIL_HOST_PASSWORD,
            use_tls=settings.EMAIL_USE_TLS,
            timeout=settings.EMAIL_TIMEOUT,
        )
    return SMTPEmailBackend(
        host=config.host,
        port=config.port,
        username=config.username,
        password=config.get_password(),
        use_tls=config.use_tls,
        use_ssl=config.use_ssl,
        timeout=config.timeout,
    )


def test_connection(config: EmailConfiguration | None = None) -> tuple[bool, str]:
    backend = build_backend(config)
    try:
        backend.open()
        backend.close()
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator
        logger.warning(
            "smtp_connection_failed host=%s port=%s error=%s",
            config.host if config is not None else settings.EMAIL_HOST,
            config.port if config is not None else settings.EMAIL_PORT,
            str(exc)[:300],
        )
        return False, str(exc)[:1000]
    return True, ""


def send_test_email(
    *,
    config: EmailConfiguration | None,
    to_email: str,
) -> tuple[bool, str]:
    config = config or get_active_config()
    backend = build_backend(config)
    sender = config.sender if config is not None else settings.DEFAULT_FROM_EMAIL
    message = EmailMessage(
        subject=_("DokkiMashora email configuration test"),
        body=_(
            "This is a test message from DokkiMashora. "
            "If you received it, the active email configuration is working."
        ),
        from_email=sender,
        to=[to_email],
        connection=backend,
    )
    try:
        message.send(fail_silently=False)
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator
        logger.warning(
            "smtp_test_email_failed recipient=%s host=%s error=%s",
            to_email,
            config.host if config is not None else settings.EMAIL_HOST,
            str(exc)[:300],
        )
        return False, str(exc)[:1000]
    return True, ""


def activate_configuration(config: EmailConfiguration) -> EmailConfiguration:
    with transaction.atomic():
        # Serialize activation across concurrent staff requests before the
        # partial unique constraint decides the winner.
        list(EmailConfiguration.objects.select_for_update().values_list("pk", flat=True))
        EmailConfiguration.objects.filter(is_active=True).exclude(pk=config.pk).update(
            is_active=False,
            updated_at=timezone.now(),
        )
        if not config.is_active:
            config.is_active = True
            config.save(update_fields=["is_active", "updated_at"])
    return config


def save_configuration(config: EmailConfiguration) -> EmailConfiguration:
    """Save a configuration while preserving the one-active-row invariant."""
    with transaction.atomic():
        list(EmailConfiguration.objects.select_for_update().values_list("pk", flat=True))
        if config.is_active:
            EmailConfiguration.objects.filter(is_active=True).exclude(pk=config.pk).update(
                is_active=False,
                updated_at=timezone.now(),
            )
        config.save()
    return config


def record_test_result(
    config: EmailConfiguration,
    *,
    ok: bool,
    error: str = "",
) -> EmailConfiguration:
    config.last_tested_at = timezone.now()
    config.last_test_ok = ok
    config.last_test_error = error[:1000]
    config.save(update_fields=["last_tested_at", "last_test_ok", "last_test_error", "updated_at"])
    return config
