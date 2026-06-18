"""Custom user model for the LMS."""

from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

from .constants import GROUP_STUDENT, STAFF_ROLE_GROUPS, USER_STATUS_ACTIVE, USER_STATUS_CHOICES
from .managers import UserManager


class User(AbstractBaseUser, PermissionsMixin, TimeStampedModel):
    """Email-based user with a stable primary role slug."""

    email = models.EmailField(_("email address"), unique=True, db_index=True)
    full_name = models.CharField(_("full name"), max_length=150, blank=True)
    full_name_ar = models.CharField(
        _("full name (Arabic)"),
        max_length=150,
        blank=True,
        help_text=_("Used for the Arabic UI."),
    )
    phone = models.CharField(
        _("phone number"),
        max_length=32,
        blank=True,
        db_index=True,
    )
    national_id = models.CharField(
        _("national ID"),
        max_length=32,
        blank=True,
        db_index=True,
        help_text=_("Stored for matching across applications."),
    )
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=USER_STATUS_CHOICES,
        default=USER_STATUS_ACTIVE,
        db_index=True,
    )

    is_staff = models.BooleanField(_("staff status"), default=False)
    is_active = models.BooleanField(_("active"), default=True)
    date_joined = models.DateTimeField(_("date joined"), default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        ordering = ("-date_joined",)
        indexes = [
            models.Index(fields=("email",)),
            models.Index(fields=("status",)),
        ]

    # --- Django protocol ----------------------------------------------------
    def __str__(self) -> str:
        return self.full_name or self.email

    def get_full_name(self) -> str:
        return self.full_name or self.email

    def get_short_name(self) -> str:
        return (self.full_name or self.email).split(" ")[0]

    def get_absolute_url(self) -> str:
        return reverse("accounts:profile")

    # --- Domain helpers -----------------------------------------------------
    @property
    def primary_role(self) -> str:
        """Return the highest-priority group for the user, or empty string."""
        groups = set(self.groups.values_list("name", flat=True))
        for candidate in STAFF_ROLE_GROUPS + (GROUP_STUDENT,):
            if candidate in groups:
                return candidate
        return ""

    @property
    def is_staff_member(self) -> bool:
        return self.is_staff or bool(self.primary_role and self.primary_role != "Student")
