from django.urls import path

from apps.progression import views

app_name = "progression"

urlpatterns = [
    path("manage/", views.admin_dashboard, name="admin_dashboard"),
    path("manage/students/<int:student_id>/", views.admin_student_detail, name="admin_student_detail"),
    path("manage/students/<int:student_id>/evaluate/", views.admin_evaluate_semester, name="admin_evaluate_semester"),
    path("manage/students/<int:student_id>/promote/", views.admin_promote, name="admin_promote"),
    path("manage/students/<int:student_id>/demote/", views.admin_demote, name="admin_demote"),
    path("manage/students/<int:student_id>/override/", views.admin_override, name="admin_override"),
    path("manage/students/<int:student_id>/lock/", views.admin_lock, name="admin_lock"),
    path("manage/students/<int:student_id>/unlock/", views.admin_unlock, name="admin_unlock"),
    path("manage/students/<int:student_id>/retake/assign/", views.admin_assign_retake, name="admin_assign_retake"),
    path("manage/students/<int:student_id>/retake/clear/", views.admin_clear_retake, name="admin_clear_retake"),
    path("manage/students/<int:student_id>/complete/", views.admin_mark_complete, name="admin_mark_complete"),
    path("manage/students/<int:student_id>/graduate/", views.admin_mark_graduate, name="admin_mark_graduate"),
    path("my-progress/", views.student_progress, name="student_progress"),
]
