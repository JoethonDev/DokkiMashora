"""Admin registrations for the communications app."""

from django.contrib import admin

from .email_message import EmailMessage
from .models import EmailTemplate


@admin.register(EmailTemplate)
class EmailTemplateAdmin(admin.ModelAdmin):
    list_display = ("slug", "name", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("slug", "name", "subject")
    readonly_fields = ("created_at", "updated_at")


@admin.register(EmailMessage)
class EmailMessageAdmin(admin.ModelAdmin):
    list_display = ("to_email", "template_slug", "status", "sent_at", "created_at")
    list_filter = ("status", "template_slug")
    search_fields = ("to_email", "subject")
    readonly_fields = (
        "template_slug",
        "to_email",
        "cc_emails",
        "subject",
        "body",
        "status",
        "error",
        "sent_by",
        "sent_at",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request) -> bool:  # noqa: ARG002
        return False
