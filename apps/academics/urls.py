"""URL configuration for the academics app."""

from django.urls import path

from apps.academics import views

app_name = "academics"

urlpatterns = [
    # Admin: diplomas
    path("manage/diplomas/", views.admin_diploma_list, name="admin_diploma_list"),
    path("manage/diplomas/create/", views.admin_diploma_create, name="admin_diploma_create"),
    path("manage/diplomas/<int:diploma_id>/", views.admin_diploma_detail, name="admin_diploma_detail"),
    path("manage/diplomas/<int:diploma_id>/edit/", views.admin_diploma_edit, name="admin_diploma_edit"),
    path("manage/diplomas/<int:diploma_id>/enrollments/", views.admin_diploma_enrollment_list, name="admin_diploma_enrollment_list"),
    # Admin: semesters
    path(
        "manage/diplomas/<int:diploma_id>/semesters/create/",
        views.admin_semester_create,
        name="admin_semester_create",
    ),
    path(
        "manage/diplomas/<int:diploma_id>/semesters/<int:semester_id>/edit/",
        views.admin_semester_edit,
        name="admin_semester_edit",
    ),
    path(
        "manage/diplomas/<int:diploma_id>/semesters/<int:semester_id>/delete/",
        views.admin_semester_delete,
        name="admin_semester_delete",
    ),
    path(
        "manage/semester-courses/<int:sc_id>/delete/",
        views.admin_semester_course_delete,
        name="admin_semester_course_delete",
    ),
    # Admin: cohorts
    path(
        "manage/diplomas/<int:diploma_id>/cohorts/create/",
        views.admin_cohort_create,
        name="admin_cohort_create",
    ),
    path(
        "manage/diplomas/<int:diploma_id>/cohorts/<int:cohort_id>/edit/",
        views.admin_cohort_edit,
        name="admin_cohort_edit",
    ),
    # Admin: courses
    path("manage/courses/", views.admin_course_list, name="admin_course_list"),
    path("manage/courses/create/", views.admin_course_create, name="admin_course_create"),
    path("manage/diplomas/semester-options/", views.admin_diploma_semester_options, name="admin_diploma_semester_options"),
    path("manage/courses/<int:course_id>/", views.admin_course_detail, name="admin_course_detail"),
    path("manage/courses/<int:course_id>/edit/", views.admin_course_edit, name="admin_course_edit"),
    path("manage/courses/<int:course_id>/enrollments/", views.admin_course_enrollment_list, name="admin_course_enrollment_list"),
    # Admin: course versions
    path(
        "manage/courses/<int:course_id>/versions/create/",
        views.admin_version_create,
        name="admin_version_create",
    ),
    path(
        "manage/courses/<int:course_id>/versions/<int:version_id>/",
        views.admin_version_detail,
        name="admin_version_detail",
    ),
    path(
        "manage/courses/<int:course_id>/versions/<int:version_id>/delete/",
        views.admin_version_delete,
        name="admin_version_delete",
    ),
    # Admin: lectures
    path(
        "manage/courses/<int:course_id>/versions/<int:version_id>/lectures/create/",
        views.admin_lecture_create,
        name="admin_lecture_create",
    ),
    path(
        "manage/courses/<int:course_id>/versions/<int:version_id>/lectures/<int:lecture_id>/edit/",
        views.admin_lecture_edit,
        name="admin_lecture_edit",
    ),
    path(
        "manage/courses/<int:course_id>/versions/<int:version_id>/lectures/<int:lecture_id>/delete/",
        views.admin_lecture_delete,
        name="admin_lecture_delete",
    ),
    path(
        "manage/courses/<int:course_id>/versions/<int:version_id>/lectures/<int:lecture_id>/preview/",
        views.admin_lecture_preview,
        name="admin_lecture_preview",
    ),
    # Student: content access
    path("my-courses/", views.student_course_list, name="student_course_list"),
    path("my-courses/<int:version_id>/", views.student_course_detail, name="student_course_detail"),
    path(
        "my-courses/<int:version_id>/lectures/<int:lecture_id>/",
        views.student_lecture_view,
        name="student_lecture_view",
    ),
    path(
        "my-courses/<int:version_id>/lectures/<int:lecture_id>/audio/<path:file_path>",
        views.student_lecture_audio,
        name="student_lecture_audio",
    ),
    path(
        "my-courses/<int:version_id>/lectures/<int:lecture_id>/audio-download/",
        views.student_lecture_audio_download,
        name="student_lecture_audio_download",
    ),
]
