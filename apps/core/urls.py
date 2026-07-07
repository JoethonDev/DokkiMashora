"""URL patterns for the core app."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.landing, name="landing"),
    path("manage/landing/", views.landing_settings, name="landing_settings"),
    path("design-showcase/", views.design_showcase, name="design_showcase"),
]
