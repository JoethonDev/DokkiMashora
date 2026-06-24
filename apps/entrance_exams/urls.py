from django.urls import path

from . import views

app_name = "entrance_exams"

urlpatterns = [
    path("", views.student_list, name="student_list"),
    path("<int:exam_id>/instructions/", views.exam_instructions, name="exam_instructions"),
    path("<int:exam_id>/start/", views.start_exam, name="start_exam"),
    path("<int:exam_id>/take/", views.take_exam, name="take_exam"),
    path("<int:exam_id>/submit/", views.submit_exam, name="submit_exam"),
    path("<int:exam_id>/results/", views.exam_results, name="results"),
    path("manage/", views.admin_list, name="admin_list"),
    path("manage/create/", views.admin_create_exam, name="admin_create_exam"),
    path("manage/<int:exam_id>/", views.admin_detail, name="admin_detail"),
    path("manage/<int:exam_id>/edit/", views.admin_edit_exam, name="admin_edit_exam"),
    path("manage/<int:exam_id>/preview/", views.admin_preview, name="admin_preview"),
    path("manage/<int:exam_id>/toggle-active/", views.admin_toggle_active, name="admin_toggle_active"),
    path("manage/<int:exam_id>/questions/add/", views.admin_add_question, name="admin_add_question"),
    path("manage/<int:exam_id>/questions/<int:question_id>/edit/", views.admin_edit_question, name="admin_edit_question"),
    path("manage/<int:exam_id>/questions/<int:question_id>/delete/", views.admin_delete_question, name="admin_delete_question"),
    path("manage/grading/", views.admin_grade_list, name="admin_grade_list"),
    path("manage/grading/<int:attempt_id>/", views.admin_grade_attempt, name="admin_grade_attempt"),
    path("manage/<int:exam_id>/emails/", views.admin_cohort_email, name="admin_cohort_email"),
    path("manage/<int:exam_id>/emails/<int:app_id>/resend/", views.admin_app_email_resend, name="admin_app_email_resend"),
    path("emails/log/", views.admin_email_log_list, name="admin_email_log_list"),
]
