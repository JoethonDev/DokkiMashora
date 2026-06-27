from django.urls import path

from apps.assessments import views

app_name = "assessments"

urlpatterns = [
    path("", views.student_list, name="student_list"),
    path("<int:assessment_id>/instructions/", views.assessment_instructions, name="instructions"),
    path("<int:assessment_id>/start/", views.start_assessment_view, name="start"),
    path("<int:assessment_id>/take/", views.take_assessment, name="take"),
    path("<int:assessment_id>/submit/", views.submit_assessment_view, name="submit"),
    path("<int:assessment_id>/submit-homework/", views.submit_homework_view, name="submit_homework"),
    path("<int:assessment_id>/results/", views.assessment_results, name="results"),
    path("manage/", views.admin_list, name="admin_list"),
    path("manage/scope-fields/", views.admin_scope_fields, name="admin_scope_fields"),
    path("manage/create/", views.admin_create, name="admin_create"),
    path("manage/<int:assessment_id>/", views.admin_detail, name="admin_detail"),
    path("manage/<int:assessment_id>/edit/", views.admin_edit, name="admin_edit"),
    path("manage/<int:assessment_id>/preview/", views.admin_preview, name="admin_preview"),
    path("manage/<int:assessment_id>/toggle/", views.admin_toggle_active, name="admin_toggle"),
    path(
        "manage/<int:assessment_id>/questions/form/",
        views.admin_question_form,
        name="admin_question_form",
    ),
    path(
        "manage/<int:assessment_id>/questions/save/",
        views.admin_question_save,
        name="admin_question_save",
    ),
    path(
        "manage/<int:assessment_id>/questions/<int:question_id>/form/",
        views.admin_question_form,
        name="admin_question_form",
    ),
    path(
        "manage/<int:assessment_id>/questions/<int:question_id>/save/",
        views.admin_question_save,
        name="admin_question_save",
    ),
    path(
        "manage/<int:assessment_id>/questions/<int:question_id>/delete/",
        views.admin_question_delete,
        name="admin_question_delete",
    ),
    path("manage/grading/", views.admin_grade_list, name="admin_grade_list"),
    path(
        "manage/grading/<int:attempt_id>/",
        views.admin_grade_detail,
        name="admin_grade_detail",
    ),
    path(
        "manage/grading/<int:attempt_id>/release/",
        views.admin_release_grades,
        name="admin_release_grades",
    ),
    path(
        "manage/grading/<int:attempt_id>/hide/",
        views.admin_hide_grades,
        name="admin_hide_grades",
    ),
    path(
        "manage/grading/answer/<int:answer_id>/grade/",
        views.admin_grade_answer,
        name="admin_grade_answer",
    ),
    path(
        "manage/grading/homework/<int:submission_id>/grade/",
        views.admin_grade_homework_submission,
        name="admin_grade_homework",
    ),
    path(
        "manage/homework/",
        views.admin_homework_grade_list,
        name="admin_homework_list",
    ),
    path(
        "manage/homework/<int:submission_id>/",
        views.admin_homework_grade_detail,
        name="admin_homework_grade_detail",
    ),
]
