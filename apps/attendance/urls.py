from django.urls import path

from apps.attendance import views

app_name = "attendance"

urlpatterns = [
    path("sessions/", views.attendance_session_list, name="attendance_session_list"),
    path("sessions/create/", views.attendance_session_create, name="attendance_session_create"),
    path("periods/<int:period_id>/", views.attendance_period_detail, name="attendance_period_detail"),
    path("periods/<int:period_id>/days/create/", views.attendance_period_day_create, name="attendance_period_day_create"),
    path("periods/<int:period_id>/close/", views.attendance_period_close, name="attendance_period_close"),
    path("sessions/<int:session_id>/", views.attendance_session_detail, name="attendance_session_detail"),
    path("sessions/<int:session_id>/open/", views.attendance_session_open, name="attendance_session_open"),
    path("sessions/<int:session_id>/close/", views.attendance_session_close, name="attendance_session_close"),
    path("sessions/<int:session_id>/delete/", views.attendance_session_delete, name="attendance_session_delete"),
    path("sessions/<int:session_id>/records/", views.attendance_session_records, name="attendance_session_records"),
    path("scan/", views.attendance_scan, name="attendance_scan"),
    path("scan/api/", views.attendance_scan_api, name="attendance_scan_api"),
    path("my-attendance/", views.my_attendance, name="my_attendance"),
]
