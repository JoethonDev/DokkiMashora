from django.contrib import admin

from .models import CohortAcademicYear


@admin.register(CohortAcademicYear)
class CohortAcademicYearAdmin(admin.ModelAdmin):
    list_display = ("cohort", "year_number", "start_date", "end_date", "semesters_per_year")
    list_filter = ("year_number",)
    search_fields = ("cohort__name", "cohort__diploma__title")
