from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class IntakeFormsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.intake_forms"
    verbose_name = _("Intake Forms")
