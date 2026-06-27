"""Django system checks that run at startup.

These fail-closed: a misconfiguration prevents the app from booting instead of
silently breaking at call time.
"""

from __future__ import annotations

from django.conf import settings
from django.core.checks import Error, register

from apps.storage.models import StoredFile


@register()
def r2_storage_production_check(app_configs, **kwargs) -> list[Error]:
    """In production, protected uploads must not sit on local storage.

    Triggers an Error if all of:
      * DEBUG is False (i.e. production-like runtime),
      * PRODUCTION_DEPLOYMENT is True,
      * USE_R2_STORAGE is False,
      * at least one StoredFile has been recorded locally with private visibility.
    """
    errors: list[Error] = []
    if settings.DEBUG:
        return errors
    if not getattr(settings, "PRODUCTION_DEPLOYMENT", False):
        return errors
    if getattr(settings, "USE_R2_STORAGE", False):
        return errors

    try:
        protected_count = StoredFile.objects.filter(
            storage_backend="local", visibility="private",
        ).count()
    except Exception:  # noqa: BLE001 - storage app may not be migrated yet
        return errors

    if protected_count > 0:
        errors.append(
            Error(
                "Protected uploads are recorded on local storage in production.",
                hint=(
                    "Set USE_R2_STORAGE=True and configure R2_ACCESS_KEY_ID, "
                    "R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME, R2_ENDPOINT_URL."
                ),
                id="deployment.E001",
            )
        )
    return errors
