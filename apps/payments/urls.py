from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("", views.upload_list, name="upload_list"),
    path("upload/", views.upload_create, name="upload_create"),
    path("upload/<int:upload_id>/", views.upload_detail, name="upload_detail"),
    path(
        "upload/<int:upload_id>/confirm/",
        views.upload_confirm,
        name="upload_confirm",
    ),
    path("records/", views.payment_record_list, name="record_list"),
    path(
        "records/<int:record_id>/",
        views.payment_record_detail,
        name="record_detail",
    ),
    path(
        "enrollment/<int:student_id>/<str:enrollment_type>/<int:enrollment_id>/toggle/",
        views.enrollment_payment_toggle,
        name="enrollment_payment_toggle",
    ),
]
