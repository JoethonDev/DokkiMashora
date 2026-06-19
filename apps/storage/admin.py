from django.contrib import admin

from .models import StoredFile


@admin.register(StoredFile)
class StoredFileAdmin(admin.ModelAdmin):
    list_display = ("original_filename", "content_type", "byte_size", "owner", "created_at")
    list_filter = ("storage_backend", "content_type", "visibility")
    search_fields = ("original_filename", "key")
    readonly_fields = ("key", "created_at")
