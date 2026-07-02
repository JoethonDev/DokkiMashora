from django.contrib import admin

from apps.exams.models import Answer, Assignment, Choice, Exam, ExamSnapshot, Question
from apps.exams.models import EmailLog


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 1


class ChoiceInline(admin.TabularInline):
    model = Choice
    extra = 1


@admin.register(Exam)
class ExamAdmin(admin.ModelAdmin):
    list_display = ("title", "exam_type", "is_active", "open_at", "close_at", "created_at")
    list_filter = ("exam_type", "is_active")
    search_fields = ("title",)
    inlines = [QuestionInline]


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("text", "exam", "question_type", "points", "order")
    list_filter = ("question_type",)
    inlines = [ChoiceInline]


@admin.register(Choice)
class ChoiceAdmin(admin.ModelAdmin):
    list_display = ("text", "question", "is_correct", "order")


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ("student", "exam", "status", "score", "assigned_at", "submitted_at")
    list_filter = ("status",)
    search_fields = ("student__email",)


@admin.register(Answer)
class AnswerAdmin(admin.ModelAdmin):
    list_display = ("assignment", "question", "score", "graded_at")


@admin.register(ExamSnapshot)
class ExamSnapshotAdmin(admin.ModelAdmin):
    list_display = ("assignment", "question", "snapshot_at")
    list_filter = ("snapshot_at",)
    list_select_related = ("assignment", "question")


@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    list_display = ("recipient", "exam", "template_slug", "sent_at", "status")
    list_filter = ("status", "template_slug")
    search_fields = ("recipient",)
