"""Admin registration for student profiles."""

from django.contrib import admin

from .models import StudentProfile


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = ("full_name", "user", "phone", "national_id", "education", "date_of_birth")
    list_filter = ("education", "gender")
    search_fields = ("full_name", "user__email", "phone", "national_id", "specialization")
    raw_id_fields = ("user",)
    readonly_fields = ("created_at", "updated_at")
