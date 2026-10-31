from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.audit"
    verbose_name = _("Audit")

    def ready(self) -> None:
        from . import signals  # noqa: F401, PLC0415 - app registry must be ready first
