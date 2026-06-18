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
]
