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
    path("documents/upload/<str:token>/", views.document_upload, name="document_upload"),
    path("documents/<int:upload_id>/persona/download/", views.download_persona_image, name="download_persona"),
    path("documents/<int:upload_id>/attachments/download/", views.download_attachments_pdf, name="download_attachments"),
    path("apply/", views.dashboard_apply, name="dashboard_apply"),
    path("program-targets/", views.program_targets, name="program_targets"),
]
