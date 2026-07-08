from __future__ import annotations

from django.urls import path

from . import views

app_name = "intake_forms"

urlpatterns = [
    # Admin: form builder
    path("admin/forms/", views.admin_intake_form_list, name="admin_intake_form_list"),
    path("admin/forms/create/", views.admin_intake_form_create, name="admin_intake_form_create"),
    path("admin/forms/<int:form_id>/", views.admin_intake_form_detail, name="admin_intake_form_detail"),
    path("admin/forms/<int:form_id>/fields/<int:field_id>/delete/", views.admin_intake_form_field_delete, name="admin_intake_form_field_delete"),
    # Admin: responses from targets
    path("admin/versions/<int:version_id>/intake/", views.admin_version_intake_responses, name="admin_version_intake_responses"),
    path("admin/cohorts/<int:cohort_id>/intake/", views.admin_cohort_intake_responses, name="admin_cohort_intake_responses"),
    # Admin: send intake request
    path("admin/applications/<int:application_id>/request-intake/", views.admin_intake_request_send, name="admin_intake_request_send"),
    # Student: fill form via token
    path("forms/fill/<str:token>/", views.fill_intake_form, name="fill_intake_form"),
]
