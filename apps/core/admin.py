"""Admin registrations for the core app."""

from django.contrib import admin

from .forms import LandingAnnouncementForm
from .models import LandingAnnouncement, LandingPageSettings


@admin.register(LandingAnnouncement)
class LandingAnnouncementAdmin(admin.ModelAdmin):
    form = LandingAnnouncementForm
    list_display = ("title_ar", "title_en", "is_active", "starts_at", "ends_at", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("title_ar", "title_en", "message_ar", "message_en")


@admin.register(LandingPageSettings)
class LandingPageSettingsAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not LandingPageSettings.objects.exists()
