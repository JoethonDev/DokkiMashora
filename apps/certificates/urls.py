from django.urls import path

from apps.certificates import views

app_name = "certificates"

urlpatterns = [
    path("manage/templates/", views.admin_template_list, name="admin_template_list"),
    path("manage/templates/create/", views.admin_template_create, name="admin_template_create"),
    path("manage/templates/design-preview/", views.admin_template_design_preview, name="admin_template_design_preview"),
    path("manage/templates/<int:template_id>/edit/", views.admin_template_edit, name="admin_template_edit"),
    path("manage/templates/<int:template_id>/delete/", views.admin_template_delete, name="admin_template_delete"),
    path("manage/templates/<int:template_id>/preview/", views.admin_template_preview, name="admin_template_preview"),
    path("manage/templates/<int:template_id>/preview-generated/", views.admin_template_generated_preview, name="admin_template_generated_preview"),
    path("manage/templates/<int:template_id>/source/upload/", views.admin_template_upload_source, name="admin_template_upload_source"),
    path("manage/templates/<int:template_id>/source/clear/", views.admin_template_clear_source, name="admin_template_clear_source"),
    path("manage/templates/<int:template_id>/editor/", views.admin_template_editor, name="admin_template_editor"),
    path("manage/templates/<int:template_id>/mapping/save/", views.admin_template_save_mapping, name="admin_template_save_mapping"),
    path("manage/templates/<int:template_id>/preview-generate/", views.admin_template_preview_generate, name="admin_template_preview_generate"),
    path("manage/", views.admin_certificate_list, name="admin_certificate_list"),
    path("manage/generate/<int:student_id>/", views.admin_generate_certificate, name="admin_generate_certificate"),
    path("manage/generate/course/<int:enrollment_id>/", views.admin_generate_course_certificate, name="admin_generate_course_certificate"),
    path("manage/<int:cert_id>/revoke/", views.admin_revoke_certificate, name="admin_revoke_certificate"),
    path("manage/<int:cert_id>/regenerate/", views.admin_regenerate_certificate, name="admin_regenerate_certificate"),
    path("manage/<int:cert_id>/download/", views.admin_download_certificate, name="admin_download_certificate"),
    path("", views.student_certificate_list, name="student_certificate_list"),
    path("<int:cert_id>/download/", views.student_certificate_download, name="student_certificate_download"),
]
