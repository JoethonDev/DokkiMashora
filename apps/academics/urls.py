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
    path("manage/diplomas/<int:diploma_id>/delete/", views.admin_diploma_delete, name="admin_diploma_delete"),
    path("manage/diplomas/<int:diploma_id>/enrollments/", views.admin_diploma_enrollment_list, name="admin_diploma_enrollment_list"),
    path("manage/diplomas/<int:diploma_id>/enrollments/<int:enrollment_id>/update/", views.admin_diploma_enrollment_update, name="admin_diploma_enrollment_update"),
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
    path(
        "manage/diplomas/<int:diploma_id>/cohorts/<int:cohort_id>/delete/",
        views.admin_cohort_delete,
        name="admin_cohort_delete",
    ),
    # Admin: courses
    path("manage/courses/", views.admin_course_list, name="admin_course_list"),
    path("manage/courses/create/", views.admin_course_create, name="admin_course_create"),
    path("manage/diplomas/semester-options/", views.admin_diploma_semester_options, name="admin_diploma_semester_options"),
    path("manage/courses/<int:course_id>/", views.admin_course_detail, name="admin_course_detail"),
    path("manage/courses/<int:course_id>/edit/", views.admin_course_edit, name="admin_course_edit"),
    path("manage/courses/<int:course_id>/delete/", views.admin_course_delete, name="admin_course_delete"),
    path("manage/courses/<int:course_id>/enrollments/", views.admin_course_enrollment_list, name="admin_course_enrollment_list"),
    path("manage/courses/<int:course_id>/enrollments/<int:enrollment_id>/migrate/", views.admin_course_enrollment_migrate, name="admin_course_enrollment_migrate"),
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
    path(
        "manage/courses/<int:course_id>/versions/<int:version_id>/toggle-active/",
        views.admin_version_toggle_active,
        name="admin_version_toggle_active",
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
    # Admin: diploma lectures (Phase 6 direct-lecture topology)
    path(
        "manage/diplomas/<int:diploma_id>/cohorts/<int:cohort_id>/semesters/<int:semester_id>/lectures/",
        views.admin_diploma_lecture_list,
        name="admin_diploma_lecture_list",
    ),
    path(
        "manage/diplomas/<int:diploma_id>/cohorts/<int:cohort_id>/semesters/<int:semester_id>/lectures/create/",
        views.admin_diploma_lecture_create,
        name="admin_diploma_lecture_create",
    ),
    path(
        "manage/diplomas/<int:diploma_id>/cohorts/<int:cohort_id>/semesters/<int:semester_id>/lectures/<int:lecture_id>/edit/",
        views.admin_diploma_lecture_edit,
        name="admin_diploma_lecture_edit",
    ),
    path(
        "manage/diplomas/<int:diploma_id>/cohorts/<int:cohort_id>/semesters/<int:semester_id>/lectures/<int:lecture_id>/delete/",
        views.admin_diploma_lecture_delete,
        name="admin_diploma_lecture_delete",
    ),
    path(
        "manage/diplomas/<int:diploma_id>/cohorts/<int:cohort_id>/semesters/<int:semester_id>/lectures/<int:lecture_id>/preview/",
        views.admin_diploma_lecture_preview,
        name="admin_diploma_lecture_preview",
    ),
    # Student: content access
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
    # Watch API
    path(
        "my-courses/<int:version_id>/lectures/<int:lecture_id>/watch/start/",
        views.api_watch_start,
        name="api_watch_start",
    ),
    path(
        "my-courses/<int:version_id>/lectures/<int:lecture_id>/watch/checkpoint/",
        views.api_watch_checkpoint,
        name="api_watch_checkpoint",
    ),
    # Audio API (P5-01)
    path(
        "my-courses/<int:version_id>/lectures/<int:lecture_id>/audio/start/",
        views.api_audio_start,
        name="api_audio_start",
    ),
    path(
        "my-courses/<int:version_id>/lectures/<int:lecture_id>/audio/checkpoint/",
        views.api_audio_checkpoint,
        name="api_audio_checkpoint",
    ),
    # Student: diploma content (Phase 6)
    path(
        "my-diplomas/<int:enrollment_id>/",
        views.student_diploma_detail,
        name="student_diploma_detail",
    ),
    path(
        "my-diplomas/<int:enrollment_id>/semesters/<int:semester_id>/",
        views.student_diploma_semester_detail,
        name="student_diploma_semester_detail",
    ),
    path(
        "my-diplomas/<int:enrollment_id>/semesters/<int:semester_id>/lectures/<int:lecture_id>/",
        views.student_diploma_lecture_view,
        name="student_diploma_lecture_view",
    ),
    path(
        "my-diplomas/<int:enrollment_id>/semesters/<int:semester_id>/lectures/<int:lecture_id>/audio/<path:file_path>",
        views.student_diploma_lecture_audio,
        name="student_diploma_lecture_audio",
    ),
    path(
        "my-diplomas/<int:enrollment_id>/semesters/<int:semester_id>/lectures/<int:lecture_id>/audio-download/",
        views.student_diploma_lecture_audio_download,
        name="student_diploma_lecture_audio_download",
    ),
    path(
        "my-diplomas/<int:enrollment_id>/semesters/<int:semester_id>/lectures/<int:lecture_id>/watch/start/",
        views.api_diploma_watch_start,
        name="api_diploma_watch_start",
    ),
    path(
        "my-diplomas/<int:enrollment_id>/semesters/<int:semester_id>/lectures/<int:lecture_id>/watch/checkpoint/",
        views.api_diploma_watch_checkpoint,
        name="api_diploma_watch_checkpoint",
    ),
]
