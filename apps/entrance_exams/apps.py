from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class EntranceExamsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.entrance_exams"
    verbose_name = _("Entrance Exams")
