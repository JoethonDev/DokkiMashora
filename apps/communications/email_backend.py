"""Email backend that uses the active database configuration when present.

Environment SMTP settings remain the fallback, so existing deployments do not
need a database row before they can send mail.
"""

from __future__ import annotations

from django.conf import settings
from django.core.mail.backends.smtp import EmailBackend as SMTPEmailBackend

from .email_config import get_active_config


class DatabaseEmailBackend(SMTPEmailBackend):
    def __init__(self, *args, **kwargs):
        self._database_config = get_active_config()
        config = self._database_config
        if config is not None:
            kwargs.setdefault("host", config.host)
            kwargs.setdefault("port", config.port)
            kwargs.setdefault("username", config.username)
            kwargs.setdefault("password", config.get_password())
            kwargs.setdefault("use_tls", config.use_tls)
            kwargs.setdefault("use_ssl", config.use_ssl)
            kwargs.setdefault("timeout", config.timeout)
        super().__init__(*args, **kwargs)

    def send_messages(self, email_messages):
        config = self._database_config
        if config is not None:
            for message in email_messages:
                if (
                    not getattr(message, "from_email", "")
                    or message.from_email == settings.DEFAULT_FROM_EMAIL
                ):
                    message.from_email = config.sender
        return super().send_messages(email_messages)
