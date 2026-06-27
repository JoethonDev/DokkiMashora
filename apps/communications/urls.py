"""URL configuration for the communications app."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "communications"

urlpatterns = [
    path("bulk/create/", views.bulk_email_create, name="bulk_email_create"),
    path("bulk/preview/", views.bulk_email_preview, name="bulk_email_preview"),
    path("bulk/history/", views.bulk_email_history, name="bulk_email_history"),
    path("bulk/<int:send_id>/", views.bulk_email_detail, name="bulk_email_detail"),
    path("bulk/discard/", views.bulk_email_discard, name="bulk_email_discard"),
    path("templates/", views.admin_template_list, name="admin_template_list"),
    path("templates/create/", views.admin_template_create, name="admin_template_create"),
    path("templates/variables/<slug:slug>/", views.admin_template_variables, name="admin_template_variables"),
    path("templates/<slug:slug>/edit/", views.admin_template_edit, name="admin_template_edit"),
    path("templates/<slug:slug>/preview/", views.admin_template_preview, name="admin_template_preview"),
    path("templates/footer/", views.admin_footer_edit, name="admin_footer_edit"),
]
