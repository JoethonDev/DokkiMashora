"""Custom user manager that uses email as the unique identifier."""

from __future__ import annotations

from django.contrib.auth.base_user import BaseUserManager
from django.db import transaction
from django.utils.translation import gettext_lazy as _


class UserManager(BaseUserManager):
    """Manager for the custom user model that uses email as the unique id."""

    use_in_migrations = True

    def _create_user(
        self,
        email: str,
        password: str | None,
        *,
        is_staff: bool = False,
        is_superuser: bool = False,
        **extra_fields,
    ):
        if not email:
            raise ValueError(_("Email is required"))
        email = self.normalize_email(email).lower()
        user = self.model(
            email=email,
            is_staff=is_staff,
            is_superuser=is_superuser,
            **extra_fields,
        )
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    @transaction.atomic
    def create_user(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    @transaction.atomic
    def create_superuser(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError(_("Superuser must have is_staff=True."))
        if extra_fields.get("is_superuser") is not True:
            raise ValueError(_("Superuser must have is_superuser=True."))

        return self._create_user(email, password, **extra_fields)
