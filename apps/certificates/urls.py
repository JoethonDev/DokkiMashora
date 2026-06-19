from django.urls import path

from apps.certificates import views

app_name = "certificates"

urlpatterns = [
    path("manage/templates/", views.admin_template_list, name="admin_template_list"),
    path("manage/templates/create/", views.admin_template_create, name="admin_template_create"),
    path("manage/templates/<int:template_id>/edit/", views.admin_template_edit, name="admin_template_edit"),
    path("manage/", views.admin_certificate_list, name="admin_certificate_list"),
    path("manage/generate/<int:student_id>/", views.admin_generate_certificate, name="admin_generate_certificate"),
    path("manage/<int:cert_id>/revoke/", views.admin_revoke_certificate, name="admin_revoke_certificate"),
    path("manage/<int:cert_id>/regenerate/", views.admin_regenerate_certificate, name="admin_regenerate_certificate"),
    path("manage/<int:cert_id>/download/", views.admin_download_certificate, name="admin_download_certificate"),
    path("", views.student_certificate_list, name="student_certificate_list"),
    path("<int:cert_id>/download/", views.student_certificate_download, name="student_certificate_download"),
]
