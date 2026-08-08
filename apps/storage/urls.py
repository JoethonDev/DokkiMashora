"""URL patterns for the storage app (protected local file serving)."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "storage"

urlpatterns = [
    path("protected/", views.protected_download, name="protected_download"),
]
