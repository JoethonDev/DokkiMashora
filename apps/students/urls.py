"""URL configuration for the students profile portal."""

from django.urls import path
from django.views.generic import RedirectView

from apps.students import views

app_name = "students"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("learning/", views.diploma_course, name="learning"),
    path("applications/portal/", views.application_history, name="applications"),
    path("applications/", RedirectView.as_view(pattern_name="students:applications", permanent=True), name="application_history"),
    path("record/", views.record, name="record"),
    path("details/", views.details, name="details"),
    path("edit/", views.edit_profile, name="edit_profile"),
    path("apply/", views.apply, name="apply"),
    path("diploma-course/", views.diploma_course, name="diploma_course"),
    path("exams/", views.exam_results, name="exam_results"),
    path("assessments/", views.assessment_history, name="assessment_history"),
    path("progression/", views.progression_history, name="progression_history"),
]
