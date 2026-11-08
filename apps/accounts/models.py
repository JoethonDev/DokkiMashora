"""Custom user model for the LMS."""

from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

from .constants import (
    ACTIVE_ROLE_PRECEDENCE,
    GROUP_SUPER_ADMIN,
    MATCH_METHOD_CHOICES,
    MATCH_METHOD_NONE,
    ROW_STATUS_CHOICES,
    ROW_STATUS_PARSED,
    UPLOAD_STATUS_CHOICES,
    UPLOAD_STATUS_PENDING,
    role_display_label,
)
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
    country_code = models.CharField(
        _("country code"),
        max_length=2,
        default="EG",
        blank=True,
        help_text=_("ISO two-letter country code."),
    )
    national_id = models.CharField(
        _("national ID"),
        max_length=32,
        blank=True,
        db_index=True,
        help_text=_("Stored identity number (National ID or Passport ID)."),
    )
    is_staff = models.BooleanField(_("staff status"), default=False)
    is_active = models.BooleanField(_("active"), default=True)
    date_joined = models.DateTimeField(_("date joined"), default=timezone.now)
    email_activated_at = models.DateTimeField(_("email activated at"), null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        ordering = ("-date_joined",)
        indexes = [
            models.Index(fields=("email",)),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["national_id"],
                condition=~models.Q(national_id=""),
                name="uq_user_national_id_nonempty",
            ),
        ]

    # --- Django protocol ----------------------------------------------------
    def __str__(self) -> str:
        return self.full_name or self.email

    def save(self, *args, **kwargs):
        if not self.full_name_ar and self.full_name:
            self.full_name_ar = self.full_name
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = {*kwargs["update_fields"], "full_name_ar"}
        super().save(*args, **kwargs)

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
        groups = getattr(self, "_group_names_cache", None)
        if groups is None:
            groups = set(self.groups.values_list("name", flat=True))
        for candidate in ACTIVE_ROLE_PRECEDENCE:
            if candidate in groups:
                return candidate
        return ""

    @property
    def role_display(self) -> str:
        """Return the translated role label without changing the stored role."""
        role = self.primary_role
        if not role and self.is_superuser:
            role = GROUP_SUPER_ADMIN
        return role_display_label(role)

    @property
    def is_staff_member(self) -> bool:
        return self.is_staff or bool(self.primary_role and self.primary_role != "Student")


class UserUpload(TimeStampedModel):
    """A bulk user import batch from CSV."""

    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="user_uploads",
        verbose_name=_("uploaded by"),
    )
    original_filename = models.CharField(_("original filename"), max_length=255)
    file_hash = models.CharField(_("file hash"), max_length=64, blank=True)
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=UPLOAD_STATUS_CHOICES,
        default=UPLOAD_STATUS_PENDING,
        db_index=True,
    )
    total_rows = models.IntegerField(_("total rows"), default=0)
    matched_rows = models.IntegerField(_("matched rows"), default=0)
    unmatched_rows = models.IntegerField(_("unmatched rows"), default=0)
    created_rows = models.IntegerField(_("created rows"), default=0)
    updated_rows = models.IntegerField(_("updated rows"), default=0)
    failed_rows = models.IntegerField(_("failed rows"), default=0)
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)
    error_summary = models.TextField(_("error summary"), blank=True)

    class Meta:
        verbose_name = _("user upload")
        verbose_name_plural = _("user uploads")
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.original_filename} ({self.get_status_display()})"


class UserUploadRow(TimeStampedModel):
    """A single row from a bulk user import CSV."""

    upload = models.ForeignKey(
        UserUpload,
        on_delete=models.CASCADE,
        related_name="rows",
        verbose_name=_("upload"),
    )
    row_number = models.IntegerField(_("row number"))
    raw_data = models.JSONField(_("raw data"), default=dict, blank=True)
    normalized_email = models.EmailField(_("normalized email"), max_length=254, blank=True)
    normalized_national_id = models.CharField(_("normalized national ID"), max_length=32, blank=True)
    normalized_phone = models.CharField(_("normalized phone"), max_length=32, blank=True)
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=ROW_STATUS_CHOICES,
        default=ROW_STATUS_PARSED,
        db_index=True,
    )
    match_method = models.CharField(
        _("match method"),
        max_length=16,
        choices=MATCH_METHOD_CHOICES,
        default=MATCH_METHOD_NONE,
        blank=True,
    )
    matched_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="import_rows",
        verbose_name=_("matched user"),
    )
    preview_identity = models.JSONField(
        _("preview identity"),
        default=dict,
        blank=True,
        help_text=_("Non-reversible identity snapshot used to reject stale confirmations."),
    )
    action = models.CharField(
        _("action"),
        max_length=10,
        blank=True,
        help_text=_("create or update"),
    )
    error_message = models.TextField(_("error message"), blank=True)

    class Meta:
        verbose_name = _("user upload row")
        verbose_name_plural = _("user upload rows")
        ordering = ("upload", "row_number")
        unique_together = (("upload", "row_number"),)

    def __str__(self) -> str:
        return f"Row {self.row_number} — {self.get_status_display()}"
