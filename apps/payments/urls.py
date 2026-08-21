from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("", views.payment_record_list, name="index"),
    path("records/", views.payment_record_list, name="record_list"),
    path("waivers/", views.waiver_list, name="waiver_list"),
    path("imports/", views.upload_list, name="upload_list"),
    path("imports/new/", views.upload_create, name="upload_create"),
    path("imports/<int:upload_id>/", views.upload_detail, name="upload_detail"),
    path(
        "imports/<int:upload_id>/confirm/",
        views.upload_confirm,
        name="upload_confirm",
    ),
    path("upload/template/", views.download_payment_csv_template, name="download_payment_csv_template"),
    path(
        "records/<int:record_id>/",
        views.payment_record_detail,
        name="record_detail",
    ),
    path(
        "records/<int:record_id>/edit/",
        views.payment_record_edit,
        name="record_edit",
    ),
    path(
        "enrollment/<int:student_id>/<str:enrollment_type>/<int:enrollment_id>/toggle/",
        views.enrollment_payment_toggle,
        name="enrollment_payment_toggle",
    ),
    path(
        "records/<int:record_id>/delete/",
        views.payment_record_delete,
        name="record_delete",
    ),
]
