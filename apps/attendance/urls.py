from django.urls import path

from apps.attendance import views

app_name = "attendance"

urlpatterns = [
    path("semester-options/", views.semester_options_by_cohort, name="semester_options_by_cohort"),
    path("sessions/", views.attendance_session_list, name="attendance_session_list"),
    path("sessions/create/", views.attendance_session_create, name="attendance_session_create"),
    path("sessions/<int:session_id>/", views.attendance_session_detail, name="attendance_session_detail"),
    path("sessions/<int:session_id>/open/", views.attendance_session_open, name="attendance_session_open"),
    path("sessions/<int:session_id>/close/", views.attendance_session_close, name="attendance_session_close"),
    path("sessions/<int:session_id>/records/", views.attendance_session_records, name="attendance_session_records"),
    path("scan/", views.attendance_scan, name="attendance_scan"),
    path("my-attendance/", views.my_attendance, name="my_attendance"),
]
