"""URL patterns for the applications app."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "applications"

urlpatterns = [
    path("register/", views.register, name="register"),
    path("register/success/", views.registration_success, name="registration_success"),
    path("admin/", views.application_list, name="admin_list"),
    path("admin/<int:application_id>/", views.application_detail, name="admin_detail"),
]
