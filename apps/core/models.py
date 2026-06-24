"""Base models for the LMS."""

from __future__ import annotations

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class TimeStampedModel(models.Model):
    """Abstract base model with timezone-aware created/updated timestamps."""

    created_at = models.DateTimeField(default=timezone.now, editable=False, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        abstract = True
        get_latest_by = "created_at"
        ordering = ("-created_at",)


class LandingAnnouncement(TimeStampedModel):
    """Admin-controlled public landing announcement."""

    title = models.CharField(_("title"), max_length=160)
    message = models.CharField(_("message"), max_length=280)
    cta_label = models.CharField(_("button label"), max_length=80, blank=True)
    cta_url = models.CharField(_("button URL"), max_length=240, blank=True)
    starts_at = models.DateTimeField(_("starts at"), null=True, blank=True)
    ends_at = models.DateTimeField(_("ends at"), null=True, blank=True)
    is_active = models.BooleanField(_("active"), default=True, db_index=True)

    class Meta:
        verbose_name = _("landing announcement")
        verbose_name_plural = _("landing announcements")
        ordering = ("-updated_at", "-created_at")

    def __str__(self):
        return self.title

    @classmethod
    def current(cls):
        now = timezone.now()
        return (
            cls.objects.filter(is_active=True)
            .filter(models.Q(starts_at__isnull=True) | models.Q(starts_at__lte=now))
            .filter(models.Q(ends_at__isnull=True) | models.Q(ends_at__gte=now))
            .first()
        )
