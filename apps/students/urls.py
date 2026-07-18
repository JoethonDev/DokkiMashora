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
    path("diploma-course/", views.diploma_course, name="diploma_course"),
    path("diploma-course/<int:version_id>/", views.diploma_course_detail, name="diploma_course_detail"),
    path("exams/", views.exam_results, name="exam_results"),
    path("assessments/", views.assessment_history, name="assessment_history"),
    path("progression/", views.progression_history, name="progression_history"),
]
