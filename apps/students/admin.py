"""Admin registration for student profiles."""

from django.contrib import admin
from django.db import transaction

from apps.accounts.services import lock_identity_writes
from apps.audit.constants import (
    ACTION_USER_CREATED,
    ACTION_USER_UPDATED,
    SECTION_ACCOUNTS,
)
from apps.audit.services import record_audit_event

from .forms import StudentProfileAdminForm
from .models import StudentProfile


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    form = StudentProfileAdminForm
    list_display = ("full_name", "user", "phone", "national_id", "education", "date_of_birth")
    list_filter = ("education", "gender")
    search_fields = ("full_name", "user__email", "phone", "national_id", "specialization")
    raw_id_fields = ("user",)
    readonly_fields = ("created_at", "updated_at")

    def has_delete_permission(self, request, obj=None):
        # Profile deletion is only supported through the guarded account flow.
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        user = obj.user
        user_changes = {}
        for field, value in (
            ("full_name", obj.full_name),
            ("phone", ""),
            ("country_code", obj.country_code),
            ("national_id", obj.national_id),
        ):
            if getattr(user, field) != value:
                setattr(user, field, value)
                user_changes[field] = value
        if user_changes:
            user.save(update_fields=[*user_changes, "updated_at"])

        changed_fields = list(form.changed_data)
        changed_fields.extend(f"user.{field}" for field in user_changes)
        if changed_fields:
            record_audit_event(
                actor=request.user,
                action=ACTION_USER_UPDATED if change else ACTION_USER_CREATED,
                target_type="student_profile",
                target_id=str(obj.id),
                section=SECTION_ACCOUNTS,
                target_label=str(obj),
                subject_type="student",
                subject_id=str(obj.id),
                subject_label=str(obj),
                metadata={"changed_fields": changed_fields},
            )

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        if request.method == "POST":
            with transaction.atomic():
                lock_identity_writes()
                return super().changeform_view(request, object_id, form_url, extra_context)
        return super().changeform_view(request, object_id, form_url, extra_context)
