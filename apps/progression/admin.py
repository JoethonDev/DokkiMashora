from django.contrib import admin

from .forms import SemesterEvaluationConfigForm
from .models import (
    CohortAcademicYear,
    DiplomaProgressionRule,
    SemesterEvaluation,
    SemesterEvaluationConfig,
    SemesterProgressionRule,
)


@admin.register(CohortAcademicYear)
class CohortAcademicYearAdmin(admin.ModelAdmin):
    list_display = ("cohort", "year_number", "start_date", "end_date", "semesters_per_year")
    list_filter = ("year_number",)
    search_fields = ("cohort__name", "cohort__diploma__title")


@admin.register(DiplomaProgressionRule)
class DiplomaProgressionRuleAdmin(admin.ModelAdmin):
    list_display = ("diploma", "semester_pass_percent", "final_exam_pass_percent", "is_active")
    list_filter = ("is_active",)
    search_fields = ("diploma__title",)
    readonly_fields = [f.name for f in DiplomaProgressionRule._meta.get_fields() if f.name != "id"]


@admin.register(SemesterProgressionRule)
class SemesterProgressionRuleAdmin(admin.ModelAdmin):
    list_display = ("semester", "pass_percent", "is_active")
    list_filter = ("is_active",)
    search_fields = ("semester__title",)
    readonly_fields = [f.name for f in SemesterProgressionRule._meta.get_fields() if f.name != "id"]


@admin.register(SemesterEvaluationConfig)
class SemesterEvaluationConfigAdmin(admin.ModelAdmin):
    """Exact Cohort+Semester evaluation configuration (P2-05)."""

    form = SemesterEvaluationConfigForm
    list_display = (
        "cohort", "semester", "attendance_weight_percent", "homework_weight_percent",
        "semester_exam_weight_percent", "overall_min_percent", "status",
    )
    list_filter = ("status",)
    search_fields = ("cohort__name", "semester__title")


@admin.register(SemesterEvaluation)
class SemesterEvaluationAdmin(admin.ModelAdmin):
    """Immutable calculated Semester Evaluation snapshots (P2-05)."""

    list_display = ("student", "semester", "result", "overall_percent", "finalized_at")
    list_filter = ("result", "semester")
    search_fields = ("student__full_name", "student__user__email")
    readonly_fields = [f.name for f in SemesterEvaluation._meta.get_fields() if f.name not in ("id", "created_at", "updated_at")]
