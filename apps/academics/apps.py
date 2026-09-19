"""AppConfig for the academics app."""

import importlib

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AcademicsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.academics"
    verbose_name = _("Academics")

    def ready(self) -> None:
        # Signal modules may import models, so load them after the registry is ready.
        importlib.import_module(".signals", __package__)
