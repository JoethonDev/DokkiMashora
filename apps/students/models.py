"""Student profile model."""

from __future__ import annotations

from datetime import date

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

from .constants import EDUCATION_CHOICES, GENDER_CHOICES


class StudentProfile(TimeStampedModel):
    """Stores the 15 registration fields linked to a user account."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="student_profile",
    )
    full_name = models.CharField(_("full name (4 parts)"), max_length=200)
    date_of_birth = models.DateField(_("date of birth"))
    gender = models.CharField(_("gender"), max_length=8, choices=GENDER_CHOICES)
    address = models.CharField(_("address"), max_length=255)
    phone = models.CharField(_("mobile number"), max_length=32, db_index=True)
    national_id = models.CharField(_("national ID"), max_length=32, db_index=True)
    education = models.CharField(
        _("education level"),
        max_length=16,
        choices=EDUCATION_CHOICES,
    )
    specialization = models.CharField(_("specialization"), max_length=120, blank=True)
    employer = models.CharField(_("employer"), max_length=120, blank=True)
    current_job = models.CharField(_("current job"), max_length=120, blank=True)
    church = models.CharField(_("church"), max_length=120, blank=True)
    priest_name = models.CharField(_("priest name"), max_length=120, blank=True)
    priest_contact = models.CharField(_("priest contact"), max_length=120, blank=True)

    class Meta:
        verbose_name = _("student profile")
        verbose_name_plural = _("student profiles")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("national_id",)),
            models.Index(fields=("phone",)),
        ]

    def __str__(self) -> str:
        return f"{self.full_name} ({self.user.email})"

    @property
    def age(self) -> int:
        today = date.today()
        return (
            today.year
            - self.date_of_birth.year
            - ((today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day))
        )
