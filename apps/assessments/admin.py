"""Django admin registrations for the assessments app."""

from django.contrib import admin

from .models import (
    Assessment,
    AssessmentAnswer,
    AssessmentAttempt,
    AssessmentQuestion,
    HomeworkAttachment,
    HomeworkSubmission,
)


class AssessmentQuestionInline(admin.TabularInline):
    model = AssessmentQuestion
    extra = 0
    fields = ("text", "question_type", "points", "order", "required")


class AssessmentAttemptInline(admin.TabularInline):
    model = AssessmentAttempt
    extra = 0
    fields = ("student", "attempt_number", "status", "score_points", "score_percent")
    readonly_fields = ("student", "attempt_number", "score_points", "score_percent")


@admin.register(Assessment)
class AssessmentAdmin(admin.ModelAdmin):
    list_display = (
        "title", "assessment_type", "legacy_status", "is_active", "release_mode",
        "max_attempts", "created_by",
    )
    list_filter = (
        "assessment_type", "legacy_status", "is_active", "release_mode",
        "course_version", "cohort", "semester",
    )
    search_fields = ("title",)
    inlines = [AssessmentQuestionInline, AssessmentAttemptInline]


@admin.register(AssessmentQuestion)
class AssessmentQuestionAdmin(admin.ModelAdmin):
    list_display = ("text", "assessment", "question_type", "points", "order", "required")
    list_filter = ("question_type", "required", "assessment__assessment_type")
    search_fields = ("text", "assessment__title")


@admin.register(AssessmentAttempt)
class AssessmentAttemptAdmin(admin.ModelAdmin):
    list_display = (
        "student", "assessment", "attempt_number", "status",
        "score_percent", "is_pass", "submitted_at",
    )
    list_filter = ("status", "is_pass")
    search_fields = ("student__email", "assessment__title")


@admin.register(AssessmentAnswer)
class AssessmentAnswerAdmin(admin.ModelAdmin):
    list_display = ("attempt", "question", "score", "graded_at", "graded_by")
    list_filter = ("graded_by",)


@admin.register(HomeworkSubmission)
class HomeworkSubmissionAdmin(admin.ModelAdmin):
    """Authoritative aggregate homework submission (P2-02)."""

    list_display = (
        "student", "assessment", "attempt", "status", "score",
        "legacy_status", "submitted_at",
    )
    list_filter = ("status", "legacy_status", "assessment__assessment_type")
    search_fields = ("student__email", "assessment__title")
    readonly_fields = ("score", "feedback", "reviewed_by", "reviewed_at", "attempt")


@admin.register(HomeworkAttachment)
class HomeworkAttachmentAdmin(admin.ModelAdmin):
    """Per-file attachment history preserved from the legacy per-file model."""

    list_display = (
        "student", "assessment", "submission", "original_filename", "status",
        "file_size", "submitted_at",
    )
    list_filter = ("status",)
    search_fields = ("student__email", "original_filename")
