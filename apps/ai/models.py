from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class GeminiReview(TimeStampedModel):
    target_app_label = models.CharField(_("target app label"), max_length=100)
    target_model = models.CharField(_("target model"), max_length=100)
    target_object_id = models.CharField(_("target object id"), max_length=64)
    prompt_version = models.CharField(_("prompt version"), max_length=50)
    model_name = models.CharField(_("model name"), max_length=100)
    request_ref = models.CharField(_("request ref"), max_length=255, unique=True)
    request_payload = models.JSONField(_("request payload"), default=dict, blank=True)
    response_payload = models.JSONField(_("response payload"), default=dict, blank=True)
    suggested_score = models.DecimalField(
        _("suggested score"), max_digits=5, decimal_places=2, null=True, blank=True,
    )
    suggested_feedback = models.TextField(_("suggested feedback"), blank=True)
    decision = models.CharField(_("decision"), max_length=20, default="pending")
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gemini_reviews",
    )
    decided_at = models.DateTimeField(_("decided at"), null=True, blank=True)

    class Meta:
        verbose_name = _("Gemini review")
        verbose_name_plural = _("Gemini reviews")
        ordering = ("-created_at",)
