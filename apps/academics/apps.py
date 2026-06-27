"""AppConfig for the academics app."""

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AcademicsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.academics"
    verbose_name = _("Academics")

    def ready(self) -> None:
        from . import signals  # noqa: F401
