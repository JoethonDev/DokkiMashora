from django.contrib import admin

from .models import CohortAcademicYear, DiplomaProgressionRule, SemesterProgressionRule


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
