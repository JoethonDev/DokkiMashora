from django.urls import path

from apps.exams import views

app_name = "exams"

urlpatterns = [
    path("", views.legacy_student_redirect, name="student_list"),
    path("<int:exam_id>/", views.exam_instructions, name="exam_instructions"),
    path("<int:exam_id>/start/", views.start_exam_view, name="start_exam"),
    path("<int:exam_id>/take/", views.take_exam, name="take_exam"),
    path("<int:exam_id>/submit/", views.submit_exam_view, name="submit_exam"),
    path("<int:exam_id>/results/", views.exam_results, name="results"),
    path("manage/", views.legacy_admin_redirect, name="admin_list"),
    path("manage/create/", views.admin_create_exam, name="admin_create_exam"),
    path("manage/<int:exam_id>/", views.admin_detail, name="admin_detail"),
    path("manage/<int:exam_id>/edit/", views.admin_edit_exam, name="admin_edit_exam"),
    path(
        "manage/<int:exam_id>/toggle-active/",
        views.admin_toggle_active,
        name="admin_toggle_active",
    ),
    path(
        "manage/<int:exam_id>/questions/form/",
        views.admin_question_form,
        name="admin_question_form",
    ),
    path(
        "manage/<int:exam_id>/questions/save/",
        views.admin_question_save,
        name="admin_question_save",
    ),
    path(
        "manage/<int:exam_id>/questions/<int:question_id>/form/",
        views.admin_question_form,
        name="admin_question_form",
    ),
    path(
        "manage/<int:exam_id>/questions/<int:question_id>/save/",
        views.admin_question_save,
        name="admin_question_save",
    ),
    path(
        "manage/<int:exam_id>/questions/<int:question_id>/delete/",
        views.admin_question_delete,
        name="admin_question_delete",
    ),
    path("manage/grading/", views.admin_grade_list, name="admin_grade_list"),
    path(
        "manage/grading/<int:assignment_id>/",
        views.admin_grade_assignment,
        name="admin_grade_assignment",
    ),
    path(
        "manage/<int:exam_id>/emails/",
        views.admin_exam_emails,
        name="admin_exam_emails",
    ),
    path(
        "manage/<int:exam_id>/snapshot/",
        views.save_exam_snapshot,
        name="save_exam_snapshot",
    ),
    path(
        "manage/<int:exam_id>/finalize-pending/",
        views.admin_finalize_pending,
        name="admin_finalize_pending",
    ),
]
