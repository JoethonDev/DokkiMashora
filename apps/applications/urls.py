"""URL patterns for the applications app."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "applications"

urlpatterns = [
    path("register/", views.register, name="register"),
    path("register/success/", views.registration_success, name="registration_success"),
    path("programs/", views.program_application_list, name="program_list"),
    path("programs/<int:application_id>/", views.program_application_detail, name="program_detail"),
    path("apply/", views.dashboard_apply, name="dashboard_apply"),
    path("program-diplomas/", views.program_diplomas, name="program_diplomas"),
    path("program-targets/", views.program_targets, name="program_targets"),
]
