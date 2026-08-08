from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class StoredFile(TimeStampedModel):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="stored_files",
        verbose_name=_("owner"),
    )
    storage_backend = models.CharField(_("storage backend"), max_length=50, default="local")
    bucket = models.CharField(_("bucket"), max_length=200, blank=True)
    key = models.CharField(_("key"), max_length=500, unique=True)
    content_type = models.CharField(_("content type"), max_length=100)
    byte_size = models.PositiveIntegerField(_("size (bytes)"))
    original_filename = models.CharField(_("original filename"), max_length=500)
    visibility = models.CharField(_("visibility"), max_length=20, default="private")

    class Meta:
        verbose_name = _("stored file")
        verbose_name_plural = _("stored files")
        ordering = ("-created_at",)

    def __str__(self):
        return self.original_filename


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
