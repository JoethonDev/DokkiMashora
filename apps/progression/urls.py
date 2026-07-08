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
    path("manage/components/<int:cohort_id>/<int:semester_id>/", views.admin_component_list, name="admin_component_list"),
    path("manage/components/<int:cohort_id>/<int:semester_id>/create/", views.admin_component_create, name="admin_component_create"),
    path("manage/components/<int:component_id>/edit/", views.admin_component_edit, name="admin_component_edit"),
    path("manage/components/<int:component_id>/delete/", views.admin_component_delete, name="admin_component_delete"),
    path("manage/grade-items/<int:enrollment_id>/<int:component_id>/custom/", views.admin_custom_activity_grade, name="admin_custom_activity_grade"),
    path("manage/grade-items/<int:enrollment_id>/<int:semester_id>/sync/", views.admin_grade_sync, name="admin_grade_sync"),
    path("manage/evaluate/<int:enrollment_id>/<int:semester_id>/", views.admin_semester_evaluation, name="admin_semester_evaluation"),
    path("manage/interview/<int:enrollment_id>/<int:semester_id>/request/", views.admin_request_interview, name="admin_request_interview"),
    path("manage/interview/<int:enrollment_id>/<int:semester_id>/confirm-pass/", views.admin_confirm_pass, name="admin_confirm_pass"),
    path("manage/interview/<int:enrollment_id>/<int:semester_id>/decision/", views.admin_interview_decision, name="admin_interview_decision"),
    path("manage/rejoin/create/<int:enrollment_id>/", views.admin_create_rejoin_plan, name="admin_create_rejoin_plan"),
    path("manage/rejoin/", views.admin_rejoin_plan_list, name="admin_rejoin_plan_list"),
    path("manage/rejoin/<int:plan_id>/activate/", views.admin_activate_rejoin_plan, name="admin_activate_rejoin_plan"),
    path("manage/rejoin/<int:plan_id>/cancel/", views.admin_cancel_rejoin_plan, name="admin_cancel_rejoin_plan"),
    path("my-progress/", views.student_progress, name="student_progress"),
]
