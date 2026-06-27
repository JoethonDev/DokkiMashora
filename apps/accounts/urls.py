"""URL patterns for the accounts app."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.EmailLoginView.as_view(), name="login"),
    path("logout/", views.EmailLogoutView.as_view(), name="logout"),
    path("post-login/", views.post_login, name="post_login"),
    path("profile/", views.profile, name="profile"),
    path("dashboard/", views.dashboard_router, name="dashboard"),
    path("dashboard/admin/", views.admin_dashboard, name="dashboard_admin"),
    path("dashboard/moderator/", views.moderator_dashboard, name="dashboard_moderator"),
    path("dashboard/teacher/", views.teacher_dashboard, name="dashboard_teacher"),
    path("dashboard/student/", views.student_dashboard, name="dashboard_student"),
    # Admin: user management
    path("manage/students/", views.admin_student_list, name="admin_student_list"),
    path("manage/students/<int:user_id>/", views.admin_user_detail, name="admin_user_detail"),
    path("manage/students/<int:user_id>/edit/", views.admin_user_edit, name="admin_user_edit"),
    path("manage/settings/", views.admin_settings, name="admin_settings"),
    path("manage/staff/", views.admin_staff_list, name="admin_staff_list"),
    # Staff assignments
    path("manage/staff/<int:user_id>/assignments/", views.admin_staff_assignments, name="admin_staff_assignments"),
    path("manage/staff/<int:user_id>/assignments/teacher/create/", views.admin_teacher_assignment_create, name="admin_teacher_assignment_create"),
    path("manage/staff/<int:user_id>/assignments/teacher/<int:assignment_id>/delete/", views.admin_teacher_assignment_delete, name="admin_teacher_assignment_delete"),
    path("manage/staff/<int:user_id>/assignments/moderator/create/", views.admin_moderator_assignment_create, name="admin_moderator_assignment_create"),
    path("manage/staff/<int:user_id>/assignments/moderator/<int:assignment_id>/delete/", views.admin_moderator_assignment_delete, name="admin_moderator_assignment_delete"),
    # Student clusters
    path("manage/cohorts/<int:cohort_id>/clusters/create/", views.admin_student_cluster_create, name="admin_student_cluster_create"),
    path("manage/clusters/<int:cluster_id>/members/add/", views.admin_student_cluster_membership_add, name="admin_student_cluster_membership_add"),
    path("manage/clusters/<int:cluster_id>/members/<int:membership_id>/remove/", views.admin_student_cluster_membership_remove, name="admin_student_cluster_membership_remove"),
    # CSV templates
    path("manage/students/import/template/", views.admin_download_student_csv_template, name="admin_download_student_csv_template"),
    # Bulk user import
    path("manage/students/import/", views.admin_student_import, name="admin_student_import"),
    path("manage/students/import/<int:upload_id>/", views.admin_student_import_detail, name="admin_student_import_detail"),
    path("manage/students/import/<int:upload_id>/confirm/", views.admin_student_import_confirm, name="admin_student_import_confirm"),
]
