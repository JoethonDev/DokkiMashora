from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.academics.models import Cohort, CourseVersion
from apps.applications.models import ProgramApplication
from apps.students.models import StudentProfile

from .constants import (
    FIELD_TYPE_CHOICE,
    FIELD_TYPE_CHOICES,
    INTAKE_REQUEST_STATUS_CHOICES,
    INTAKE_REQUEST_STATUS_REQUESTED,
)


class IntakeForm(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    course_version = models.ForeignKey(
        CourseVersion, on_delete=models.CASCADE,
        null=True, blank=True, related_name="intake_forms",
    )
    cohort = models.ForeignKey(
        Cohort, on_delete=models.CASCADE,
        null=True, blank=True, related_name="intake_forms",
    )
    is_required = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    open_at = models.DateTimeField(null=True, blank=True)
    close_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT,
        null=True, blank=True, related_name="intake_forms_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["course_version", "is_active", "is_required"]),
            models.Index(fields=["cohort", "is_active", "is_required"]),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def clean(self):
        if self.course_version_id and self.cohort_id:
            raise ValidationError(_("A form cannot target both a course version and a cohort."))
        if not self.course_version_id and not self.cohort_id:
            raise ValidationError(_("A form must target a course version or a cohort."))
        if self.open_at and self.close_at and self.open_at >= self.close_at:
            raise ValidationError(_("open_at must be before close_at."))

    def is_open(self) -> bool:
        if not self.is_active:
            return False
        now = timezone.now()
        if self.open_at and now < self.open_at:
            return False
        if self.close_at and now >= self.close_at:
            return False
        return True


class IntakeFormField(models.Model):
    form = models.ForeignKey(IntakeForm, on_delete=models.CASCADE, related_name="fields")
    label = models.CharField(max_length=255)
    help_text = models.CharField(max_length=500, blank=True)
    field_type = models.CharField(max_length=20, choices=FIELD_TYPE_CHOICES)
    required = models.BooleanField(default=False)
    options = models.JSONField(default=list, blank=True)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order"]
        unique_together = [("form", "order")]

    def __str__(self):
        return self.label

    def save(self, *args, **kwargs):
        if self._state.adding and not self.order:
            max_order = IntakeFormField.objects.filter(form=self.form).aggregate(m=models.Max("order"))["m"]
            self.order = (max_order or 0) + 1
        self.clean()
        super().save(*args, **kwargs)

    def clean(self):
        if self.field_type == FIELD_TYPE_CHOICE:
            if not self.options or not isinstance(self.options, list) or len(self.options) == 0:
                raise ValidationError(_("Choice fields must have at least one option."))
        else:
            if self.options:
                raise ValidationError(_("Non-choice fields must not have options."))


class IntakeFormRequest(models.Model):
    form = models.ForeignKey(IntakeForm, on_delete=models.CASCADE, related_name="requests")
    program_application = models.ForeignKey(
        ProgramApplication, on_delete=models.CASCADE, related_name="intake_requests",
    )
    status = models.CharField(
        max_length=20, choices=INTAKE_REQUEST_STATUS_CHOICES,
        default=INTAKE_REQUEST_STATUS_REQUESTED, db_index=True,
    )
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    requested_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="intake_requests_made",
    )
    requested_at = models.DateTimeField(default=timezone.now)
    submitted_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="intake_requests_cancelled",
    )

    class Meta:
        indexes = [
            models.Index(fields=["program_application", "status"]),
            models.Index(fields=["form", "status"]),
        ]

    def __str__(self):
        return f"Request for {self.form} ({self.get_status_display()})"


class IntakeFormResponse(models.Model):
    form = models.ForeignKey(IntakeForm, on_delete=models.CASCADE, related_name="responses")
    student = models.ForeignKey(
        StudentProfile, on_delete=models.CASCADE, related_name="intake_responses",
    )
    program_application = models.ForeignKey(
        ProgramApplication, on_delete=models.CASCADE, related_name="intake_responses",
    )
    request = models.OneToOneField(
        IntakeFormRequest, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="response",
    )
    submitted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = [("form", "program_application")]

    def __str__(self):
        return f"Response for {self.form} by {self.student}"


class IntakeFormAnswer(models.Model):
    response = models.ForeignKey(
        IntakeFormResponse, on_delete=models.CASCADE, related_name="answers",
    )
    field = models.ForeignKey(
        IntakeFormField, on_delete=models.CASCADE, related_name="answers",
    )
    value = models.JSONField()

    class Meta:
        unique_together = [("response", "field")]

    def __str__(self):
        return f"Answer for {self.field}"
