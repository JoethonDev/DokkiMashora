"""URL configuration for the students profile portal."""

from django.urls import path

from apps.students import views

app_name = "students"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("details/", views.details, name="details"),
    path("edit/", views.edit_profile, name="edit_profile"),
    path("applications/", views.application_history, name="application_history"),
    path("apply/", views.apply, name="apply"),
    path("enrollments/", views.enrollment_history, name="enrollment_history"),
    path("access/", views.access_list, name="access_list"),
    path("exams/", views.exam_results, name="exam_results"),
    path("assessments/", views.assessment_history, name="assessment_history"),
    path("homework/", views.homework_list, name="homework_list"),
    path("progression/", views.progression_history, name="progression_history"),
    path("certificates/", views.certificate_list, name="certificate_list"),
    path("payments/", views.payment_status, name="payment_status"),
    path("notifications/", views.notification_history, name="notification_history"),
]
