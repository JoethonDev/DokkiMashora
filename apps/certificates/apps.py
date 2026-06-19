from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CertificatesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.certificates"
    verbose_name = _("Certificates")
