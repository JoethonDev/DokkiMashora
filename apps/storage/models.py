from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class DeferredStorageDeletion(TimeStampedModel):
    """Durable record of a storage object whose post-commit deletion failed (S2-33).

    Written after commit when cleanup of a replaced/deleted lecture's audio
    object fails. A reconciliation command retries each row until the backend
    accepts the delete, so a failed remote deletion is never silently lost.
    Either ``key`` or ``prefix`` is set; a row with both is not created.
    """

    key = models.CharField(_("storage key"), max_length=500, blank=True, db_index=True)
    prefix = models.CharField(_("storage prefix"), max_length=500, blank=True, db_index=True)
    attempts = models.PositiveIntegerField(_("attempts"), default=0)
    last_error = models.TextField(_("last error"), blank=True)

    class Meta:
        verbose_name = _("deferred storage deletion")
        verbose_name_plural = _("deferred storage deletions")
        ordering = ("created_at",)
        indexes = [
            models.Index(fields=["key"]),
            models.Index(fields=["prefix"]),
        ]

    def __str__(self):
        return self.key or self.prefix
