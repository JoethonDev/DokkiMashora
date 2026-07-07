from django.contrib import admin

from .models import (
    EntranceAnswer,
    EntranceAttempt,
    EntranceChoice,
    EntranceExam,
    EntranceQuestion,
    EntranceSnapshot,
)


class EntranceQuestionInline(admin.TabularInline):
    model = EntranceQuestion
    extra = 0


class EntranceChoiceInline(admin.TabularInline):
    model = EntranceChoice
    extra = 0


class EntranceAnswerInline(admin.TabularInline):
    model = EntranceAnswer
    extra = 0
    readonly_fields = ("gemini_suggested_score", "gemini_suggested_feedback", "gemini_request_ref")


@admin.register(EntranceExam)
class EntranceExamAdmin(admin.ModelAdmin):
    list_display = ("title", "diploma", "cohort", "is_active", "open_at", "close_at", "created_at")
    list_filter = ("is_active", "diploma", "cohort")
    search_fields = ("title",)
    inlines = [EntranceQuestionInline]


@admin.register(EntranceAttempt)
class EntranceAttemptAdmin(admin.ModelAdmin):
    list_display = ("student", "exam", "status", "score", "result", "submitted_at", "graded_at")
    list_filter = ("status", "result", "exam__diploma", "exam__cohort")
    search_fields = ("student__full_name", "student__user__email", "exam__title")
    list_select_related = ("student__user", "exam")
    inlines = [EntranceAnswerInline]


@admin.register(EntranceSnapshot)
class EntranceSnapshotAdmin(admin.ModelAdmin):
    list_display = ("attempt", "question", "snapshot_at")
    list_filter = ("attempt__status",)
    list_select_related = ("attempt__student__user", "question")
