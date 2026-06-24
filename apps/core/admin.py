"""Admin registrations for the core app."""

from django.contrib import admin

from .models import LandingAnnouncement


@admin.register(LandingAnnouncement)
class LandingAnnouncementAdmin(admin.ModelAdmin):
    list_display = ("title", "is_active", "starts_at", "ends_at", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("title", "message")
