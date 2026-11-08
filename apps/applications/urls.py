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
    path(
        "manage/import/",
        views.admin_application_import,
        name="admin_application_import",
    ),
    path(
        "manage/import/template/",
        views.admin_application_import_template,
        name="admin_application_import_template",
    ),
    path(
        "manage/import/targets/",
        views.admin_application_import_targets,
        name="admin_application_import_targets",
    ),
    path(
        "manage/import/<int:import_id>/",
        views.admin_application_import_detail,
        name="admin_application_import_detail",
    ),
    path(
        "manage/import/<int:import_id>/confirm/",
        views.admin_application_import_confirm,
        name="admin_application_import_confirm",
    ),
    path("documents/upload/<str:token>/", views.document_upload, name="document_upload"),
    path("my/<int:application_id>/documents/upload/", views.my_document_upload, name="my_document_upload"),
    path("documents/<int:upload_id>/persona/download/", views.download_persona_image, name="download_persona"),
    path("documents/<int:upload_id>/attachments/download/", views.download_attachments_pdf, name="download_attachments"),
    path("program-targets/", views.program_targets, name="program_targets"),
]
