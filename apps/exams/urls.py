from django.urls import path

from apps.exams import views

app_name = "exams"

urlpatterns = [
    path("", views.student_list, name="student_list"),
    path("access/<uuid:token>/", views.exam_access, name="exam_access"),
    path(
        "access/<uuid:token>/instructions/",
        views.exam_instructions_token,
        name="exam_instructions_token",
    ),
    path("access/<uuid:token>/start/", views.start_exam_token, name="start_exam_token"),
    path("access/<uuid:token>/take/", views.take_exam_token, name="take_exam_token"),
    path(
        "access/<uuid:token>/submit/",
        views.submit_exam_token,
        name="submit_exam_token",
    ),
    path(
        "access/<uuid:token>/results/",
        views.exam_results_token,
        name="exam_results_token",
    ),
    path("<int:exam_id>/", views.exam_instructions, name="exam_instructions"),
    path("<int:exam_id>/start/", views.start_exam_view, name="start_exam"),
    path("<int:exam_id>/take/", views.take_exam, name="take_exam"),
    path("<int:exam_id>/submit/", views.submit_exam_view, name="submit_exam"),
    path("<int:exam_id>/results/", views.exam_results, name="results"),
    path("manage/", views.admin_list, name="admin_list"),
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
        "manage/grading/answer/<int:answer_id>/gemini/",
        views.admin_grade_with_gemini,
        name="admin_grade_with_gemini",
    ),
]
