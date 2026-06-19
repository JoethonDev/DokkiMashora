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
