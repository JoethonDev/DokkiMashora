"""Admin registration for applications."""

from django.contrib import admin

from .models import Application, ApplicationDecision, ApplicationEmailLog


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("student", "status", "reviewed_by", "reviewed_at", "created_at")
    list_filter = ("status",)
    search_fields = ("student__full_name", "student__user__email", "student__national_id")
    raw_id_fields = ("student", "reviewed_by")
    readonly_fields = ("created_at", "updated_at", "reviewed_at", "activated_at")
    date_hierarchy = "created_at"


@admin.register(ApplicationEmailLog)
class ApplicationEmailLogAdmin(admin.ModelAdmin):
    list_display = ("application", "action", "status", "sent_at", "sent_by", "is_resend")
    list_filter = ("action", "status", "is_resend")
    search_fields = ("recipient",)
    readonly_fields = ("created_at", "updated_at", "sent_at")


@admin.register(ApplicationDecision)
class ApplicationDecisionAdmin(admin.ModelAdmin):
    list_display = ("action", "target_type", "target_id", "actor", "old_status", "new_status", "created_at")
    list_filter = ("action", "target_type")
    search_fields = ("target_id", "actor__email")
    readonly_fields = ("created_at",)
