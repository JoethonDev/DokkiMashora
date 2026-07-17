"""Admin configuration for the academics app."""

from django.contrib import admin

from apps.academics.models import (
    Cohort,
    Course,
    CourseEnrollment,
    CourseVersion,
    Diploma,
    DiplomaEnrollment,
    Lecture,
    ModeratorStudentAssignment,
    Semester,
    TeacherAssignment,
)


@admin.register(Diploma)
class DiplomaAdmin(admin.ModelAdmin):
    list_display = ("title", "total_semesters", "is_active", "created_by")
    list_filter = ("is_active",)
    search_fields = ("title", "description")


@admin.register(Semester)
class SemesterAdmin(admin.ModelAdmin):
    list_display = ("diploma", "order", "title", "is_active")
    list_filter = ("is_active", "diploma")
    search_fields = ("title",)


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("title", "is_active", "created_by")
    list_filter = ("is_active",)
    search_fields = ("title", "description")


@admin.register(Cohort)
class CohortAdmin(admin.ModelAdmin):
    list_display = ("diploma", "name", "start_date", "end_date", "is_active")
    list_filter = ("is_active", "diploma")
    search_fields = ("name",)


@admin.register(CourseVersion)
class CourseVersionAdmin(admin.ModelAdmin):
    list_display = ("course", "label", "access_duration_days", "is_active")
    list_filter = ("is_active", "course")
    search_fields = ("label",)


@admin.register(Lecture)
class LectureAdmin(admin.ModelAdmin):
    list_display = ("course_version", "order", "title", "is_published", "release_at", "audio_processing_status")
    list_filter = ("is_published", "course_version__course", "audio_processing_status")
    search_fields = ("title", "description")


@admin.register(DiplomaEnrollment)
class DiplomaEnrollmentAdmin(admin.ModelAdmin):
    list_display = ("student", "cohort", "status", "activated_at", "expires_at")
    list_filter = ("status", "cohort")
    search_fields = ("student__full_name", "student__user__email")


@admin.register(CourseEnrollment)
class CourseEnrollmentAdmin(admin.ModelAdmin):
    list_display = ("student", "course_version", "status", "activated_at", "expires_at")
    list_filter = ("status", "course_version__course")
    search_fields = ("student__full_name", "student__user__email")


@admin.register(TeacherAssignment)
class TeacherAssignmentAdmin(admin.ModelAdmin):
    list_display = ("teacher", "scope_type", "course", "diploma", "semester", "cohort", "active", "assigned_by")
    list_filter = ("scope_type", "active")
    search_fields = ("teacher__email", "course__title", "diploma__title", "semester__title", "cohort__name")


@admin.register(ModeratorStudentAssignment)
class ModeratorStudentAssignmentAdmin(admin.ModelAdmin):
    list_display = ("moderator", "student", "cohort", "active", "assigned_by", "revoked_at")
    list_filter = ("active", "cohort")
    search_fields = ("moderator__email", "student__full_name", "student__user__email", "cohort__name")
