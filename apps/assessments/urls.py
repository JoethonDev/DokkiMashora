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
    path("<int:assessment_id>/autosave/", views.api_diploma_autosave, name="api_diploma_autosave"),
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
        "manage/<int:assessment_id>/release-all/",
        views.admin_release_all_grades,
        name="admin_release_all",
    ),
    path(
        "manage/<int:assessment_id>/hide-all/",
        views.admin_hide_all_grades,
        name="admin_hide_all",
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
    path(
        "manage/<int:assessment_id>/sections/create/",
        views.admin_section_create,
        name="admin_section_create",
    ),
    path(
        "manage/<int:assessment_id>/sections/<int:section_id>/edit/",
        views.admin_section_edit,
        name="admin_section_edit",
    ),
    path(
        "manage/<int:assessment_id>/sections/<int:section_id>/delete/",
        views.admin_section_delete,
        name="admin_section_delete",
    ),
    path(
        "manage/<int:assessment_id>/sections/<int:section_id>/manual/create/",
        views.admin_section_manual_question_create,
        name="admin_section_manual_create",
    ),
    path(
        "manage/<int:assessment_id>/sections/<int:section_id>/manual/<int:mq_id>/edit/",
        views.admin_section_manual_question_edit,
        name="admin_section_manual_edit",
    ),
    path(
        "manage/<int:assessment_id>/sections/<int:section_id>/manual/<int:mq_id>/delete/",
        views.admin_section_manual_question_delete,
        name="admin_section_manual_delete",
    ),
    # Diploma lecture bank questions (Phase 7)
    path(
        "manage/bank/<int:diploma_id>/cohorts/<int:cohort_id>/semesters/<int:semester_id>/lectures/<int:lecture_id>/",
        views.admin_lecture_bank_list,
        name="admin_lecture_bank_list",
    ),
    path(
        "manage/bank/<int:diploma_id>/cohorts/<int:cohort_id>/semesters/<int:semester_id>/lectures/<int:lecture_id>/create/",
        views.admin_lecture_bank_question_create,
        name="admin_lecture_bank_question_create",
    ),
    path(
        "manage/bank/<int:diploma_id>/cohorts/<int:cohort_id>/semesters/<int:semester_id>/lectures/<int:lecture_id>/<int:question_id>/edit/",
        views.admin_lecture_bank_question_edit,
        name="admin_lecture_bank_question_edit",
    ),
    path(
        "manage/bank/<int:diploma_id>/cohorts/<int:cohort_id>/semesters/<int:semester_id>/lectures/<int:lecture_id>/<int:question_id>/delete/",
        views.admin_lecture_bank_question_delete,
        name="admin_lecture_bank_question_delete",
    ),
    # Exam code roster (Phase 7)
    path("manage/<int:assessment_id>/exceptional-access/", views.admin_exceptional_access, name="admin_exceptional_access"),
    path("manage/<int:assessment_id>/exceptional-access/<int:override_id>/cancel/", views.admin_cancel_exceptional_access, name="admin_cancel_exceptional_access"),
    path("manage/<int:assessment_id>/roster/", views.admin_roster_list, name="admin_roster"),
    path("manage/<int:assessment_id>/roster/generate/", views.admin_generate_codes, name="admin_generate_codes"),
    path("manage/<int:assessment_id>/roster/<int:code_id>/toggle-mode/", views.admin_toggle_delivery_mode, name="admin_toggle_delivery_mode"),
    path("manage/<int:assessment_id>/roster/export-offline/", views.admin_export_offline_codes, name="admin_export_offline_codes"),
    path("manage/<int:assessment_id>/offline-grades/", views.admin_offline_grade_entry, name="admin_offline_grade_entry"),
    path("manage/<int:assessment_id>/offline-grades/<int:code_id>/<int:section_id>/", views.admin_offline_grade_section, name="admin_offline_grade_section"),
    # Reviewer routes (Phase 8)
    path("reviewer/dashboard/", views.reviewer_dashboard, name="reviewer_dashboard"),
    path("reviewer/section/<int:assignment_id>/", views.reviewer_section_grading, name="reviewer_section_grading"),
    path("manage/reviewer-assignments/", views.admin_reviewer_assignment_list, name="admin_reviewer_assignment_list"),
    path("manage/reviewer-assignments/create/", views.admin_reviewer_assignment_create, name="admin_reviewer_assignment_create"),
]
