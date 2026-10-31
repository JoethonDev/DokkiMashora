"""Admin registration for the custom user model."""

from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import Group
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from apps.audit.constants import ACTION_USER_CREATED, ACTION_USER_UPDATED, SECTION_ACCOUNTS
from apps.audit.services import record_audit_event

from .constants import (
    ACTIVE_ROLE_GROUPS,
    ACTIVE_ROLE_PRECEDENCE,
    GROUP_SUPER_ADMIN,
    STAFF_ROLE_GROUPS,
)
from .forms import AdminUserChangeForm, AdminUserCreationForm
from .models import User
from .services import lock_identity_writes


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    add_form = AdminUserCreationForm
    form = AdminUserChangeForm
    model = User
    list_display = ("email", "full_name", "phone", "is_staff", "is_active", "date_joined")
    list_filter = ("is_staff", "is_superuser", "is_active", "groups")
    search_fields = ("email", "full_name", "full_name_ar", "phone", "national_id", "country_code")
    ordering = ("-date_joined",)
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            _("Personal info"),
            {"fields": ("full_name", "full_name_ar", "country_code", "phone", "national_id")},
        ),
        (_("Status"), {"fields": ("is_active",)}),
        (
            _("Permissions"),
            {"fields": ("is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2"),
            },
        ),
    )
    readonly_fields = ("last_login", "date_joined", "is_staff")

    def has_add_permission(self, request):
        # Role-aware account creation is provided by the product's account
        # screens; Django's generic add form cannot create the required profile.
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        # All account deletion must pass through delete_user_account so linked
        # academic history is protected and the actor is audited.
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_view_permission(request, obj)

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if obj is None or getattr(obj, "student_profile", None) is None:
            return fieldsets
        identity_fields = {"full_name", "phone", "country_code", "national_id"}
        filtered = []
        for name, options in fieldsets:
            fields = tuple(field for field in options["fields"] if field not in identity_fields)
            if fields:
                filtered.append((name, {**options, "fields": fields}))
        return filtered

    def save_model(self, request, obj, form, change):
        obj._admin_groups_before = set(obj.groups.values_list("name", flat=True)) if obj.pk else set()
        old_active = form.initial.get("is_active") if change else None
        super().save_model(request, obj, form, change)
        changed_fields = list(form.changed_data)
        if changed_fields:
            metadata = {"changed_fields": changed_fields}
            if "is_active" in changed_fields:
                metadata.update({"active_before": old_active, "active_after": obj.is_active})
            record_audit_event(
                actor=request.user,
                action=ACTION_USER_UPDATED if change else ACTION_USER_CREATED,
                target_type="user",
                target_id=str(obj.id),
                section=SECTION_ACCOUNTS,
                target_label=str(obj),
                subject_type="user",
                subject_id=str(obj.id),
                subject_label=str(obj),
                metadata=metadata,
            )

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == "groups":
            allowed_roles = ACTIVE_ROLE_GROUPS
            if not request.user.is_superuser:
                allowed_roles = tuple(role for role in ACTIVE_ROLE_GROUPS if role != GROUP_SUPER_ADMIN)
            kwargs["queryset"] = Group.objects.filter(name__in=allowed_roles)
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        user = form.instance
        groups = list(user.groups.filter(name__in=ACTIVE_ROLE_GROUPS))
        group_by_name = {group.name: group for group in groups}
        before = getattr(user, "_admin_groups_before", set())
        winner = next(
            (group_by_name[name] for name in ACTIVE_ROLE_PRECEDENCE if name in group_by_name),
            None,
        )
        historical_names = before.difference(ACTIVE_ROLE_GROUPS)
        historical_groups = list(Group.objects.filter(name__in=historical_names))
        selected_groups = historical_groups + ([winner] if winner else [])
        user.groups.set(selected_groups)
        desired_staff = user.is_superuser or bool(winner and winner.name in STAFF_ROLE_GROUPS)
        staff_changed = user.is_staff != desired_staff
        if staff_changed:
            user.is_staff = desired_staff
            user.save(update_fields=["is_staff", "updated_at"])
        after = {group.name for group in selected_groups}
        if before != after or staff_changed:
            role_before = next((role for role in ACTIVE_ROLE_PRECEDENCE if role in before), "")
            role_after = winner.name if winner else ""
            record_audit_event(
                actor=request.user,
                action=ACTION_USER_UPDATED,
                target_type="user",
                target_id=str(user.id),
                section=SECTION_ACCOUNTS,
                target_label=str(user),
                subject_type="user",
                subject_id=str(user.id),
                subject_label=str(user),
                metadata={
                    "changed_fields": ["groups"] + (["is_staff"] if staff_changed else []),
                    "role_before": role_before,
                    "role_after": role_after,
                },
            )

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        if request.method == "POST":
            with transaction.atomic():
                lock_identity_writes()
                return super().changeform_view(request, object_id, form_url, extra_context)
        return super().changeform_view(request, object_id, form_url, extra_context)
