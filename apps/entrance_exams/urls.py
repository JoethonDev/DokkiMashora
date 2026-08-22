from django.urls import path

from . import views

app_name = "entrance_exams"

urlpatterns = [
    path("", views.student_list, name="student_list"),
    path("access/<uuid:token>/", views.exam_access_token, name="exam_access_token"),
    path("access/<uuid:token>/qr/", views.exam_qr_code, name="exam_qr_code"),
    path("access/<uuid:token>/start/", views.start_exam, name="start_exam"),
    path("access/<uuid:token>/take/", views.take_exam, name="take_exam"),
    path("access/<uuid:token>/submit/", views.submit_exam, name="submit_exam"),
    path("access/<uuid:token>/snapshot/", views.save_exam_snapshot, name="save_exam_snapshot"),
    path("access/<uuid:token>/finalize-expired/", views.finalize_expired_exam, name="finalize_expired_exam"),
    path("access/<uuid:token>/results/", views.exam_results, name="results"),
    path("manage/", views.admin_list, name="admin_list"),
    path("manage/cohorts-for-diploma/", views.admin_cohorts_for_diploma, name="admin_cohorts_for_diploma"),
    path("manage/create/", views.admin_create_exam, name="admin_create_exam"),
    path("manage/<int:exam_id>/", views.admin_detail, name="admin_detail"),
    path("manage/<int:exam_id>/edit/", views.admin_edit_exam, name="admin_edit_exam"),
    path("manage/<int:exam_id>/preview/", views.admin_preview, name="admin_preview"),
    path("manage/<int:exam_id>/toggle-active/", views.admin_toggle_active, name="admin_toggle_active"),
    path("manage/<int:exam_id>/finalize-pending/", views.admin_finalize_pending, name="admin_finalize_pending"),
    path("manage/<int:exam_id>/questions/add/", views.admin_add_question, name="admin_add_question"),
    path("manage/<int:exam_id>/questions/<int:question_id>/edit/", views.admin_edit_question, name="admin_edit_question"),
    path("manage/<int:exam_id>/questions/<int:question_id>/delete/", views.admin_delete_question, name="admin_delete_question"),
    path("manage/grading/", views.admin_grade_list, name="admin_grade_list"),
    path("manage/grading/<int:attempt_id>/", views.admin_grade_attempt, name="admin_grade_attempt"),
    path("manage/grading/<int:attempt_id>/decision/", views.admin_attempt_decision, name="admin_attempt_decision"),
    path("manage/<int:exam_id>/emails/", views.admin_cohort_email, name="admin_cohort_email"),
    path("manage/<int:exam_id>/emails/<int:app_id>/resend/", views.admin_app_email_resend, name="admin_app_email_resend"),
    path("emails/log/", views.admin_email_log_list, name="admin_email_log_list"),
]
