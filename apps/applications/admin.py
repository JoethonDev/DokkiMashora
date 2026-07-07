"""Admin registration for applications."""

from django.contrib import admin

from .models import (
    ProgramApplication,
    ProgramApplicationDecision,
    ProgramApplicationEmailLog,
    ProgramApplicationNote,
)


@admin.register(ProgramApplication)
class ProgramApplicationAdmin(admin.ModelAdmin):
    list_display = ("student", "program_type", "status", "reviewed_by", "reviewed_at", "created_at")
    list_filter = ("status", "program_type")
    search_fields = ("student__full_name", "student__user__email", "student__national_id")
    raw_id_fields = ("student", "reviewed_by")
    readonly_fields = ("created_at", "updated_at", "reviewed_at")
    date_hierarchy = "created_at"


@admin.register(ProgramApplicationEmailLog)
class ProgramApplicationEmailLogAdmin(admin.ModelAdmin):
    list_display = ("program_application", "action", "status", "sent_at", "sent_by", "is_resend")
    list_filter = ("action", "status", "is_resend")
    search_fields = ("recipient",)
    readonly_fields = ("created_at", "updated_at", "sent_at")


@admin.register(ProgramApplicationDecision)
class ProgramApplicationDecisionAdmin(admin.ModelAdmin):
    list_display = ("action", "program_application", "actor", "old_status", "new_status", "created_at")
    list_filter = ("action",)
    search_fields = ("program_application__student__full_name", "actor__email")
    readonly_fields = ("created_at",)


@admin.register(ProgramApplicationNote)
class ProgramApplicationNoteAdmin(admin.ModelAdmin):
    list_display = ("program_application", "actor", "created_at")
    search_fields = ("program_application__student__full_name", "actor__email")
    readonly_fields = ("created_at",)
