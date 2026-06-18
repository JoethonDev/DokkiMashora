"""Admin registration for applications."""

from django.contrib import admin

from .models import Application


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("student", "status", "reviewed_by", "reviewed_at", "created_at")
    list_filter = ("status",)
    search_fields = ("student__full_name", "student__user__email", "student__national_id")
    raw_id_fields = ("student", "reviewed_by")
    readonly_fields = ("created_at", "updated_at", "reviewed_at", "activated_at")
    date_hierarchy = "created_at"
