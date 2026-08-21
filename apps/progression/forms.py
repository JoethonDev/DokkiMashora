"""Forms for the progression app (P2-05+)."""

from __future__ import annotations

from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms import StyledFormMixin

from .constants import EVALUATION_CONFIG_STATUS_ACTIVE
from .models import SemesterEvaluationConfig


class SemesterEvaluationConfigForm(StyledFormMixin, forms.ModelForm):
    """Exact Cohort+Semester evaluation configuration form.

    Exposes exactly the three locked components (Attendance, Homework,
    Semester Exam) with weights that must total 100 and per-component/overall
    minimums. ``incomplete`` configs are blocked from finalization until
    remediated.
    """

    class Meta:
        model = SemesterEvaluationConfig
        fields = [
            "cohort",
            "semester",
            "attendance_weight_percent",
            "homework_weight_percent",
            "semester_exam_weight_percent",
            "attendance_min_percent",
            "homework_min_percent",
            "semester_exam_min_percent",
            "overall_min_percent",
            "status",
        ]
        widgets = {
            "attendance_weight_percent": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "100"}),
            "homework_weight_percent": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "100"}),
            "semester_exam_weight_percent": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "100"}),
            "attendance_min_percent": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "100"}),
            "homework_min_percent": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "100"}),
            "semester_exam_min_percent": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "100"}),
            "overall_min_percent": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "100"}),
        }
        labels = {
            "attendance_weight_percent": _("Attendance weight (%)"),
            "homework_weight_percent": _("Homework weight (%)"),
            "semester_exam_weight_percent": _("Semester exam weight (%)"),
            "attendance_min_percent": _("Attendance minimum required (%)"),
            "homework_min_percent": _("Homework minimum required (%)"),
            "semester_exam_min_percent": _("Semester exam minimum required (%)"),
            "overall_min_percent": _("Overall passing minimum (%)"),
        }
        help_texts = {
            "attendance_weight_percent": _("Percentage of the final evaluation; enter 0 to 100."),
            "homework_weight_percent": _("Percentage of the final evaluation; enter 0 to 100."),
            "semester_exam_weight_percent": _("Percentage of the final evaluation; enter 0 to 100."),
            "attendance_min_percent": _("Required attendance result; enter 0 to 100."),
            "homework_min_percent": _("Required homework result; enter 0 to 100."),
            "semester_exam_min_percent": _("Required semester exam result; enter 0 to 100."),
            "overall_min_percent": _("Required final result; enter 0 to 100."),
        }

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get("status")
        if status == EVALUATION_CONFIG_STATUS_ACTIVE:
            total = (
                (cleaned.get("attendance_weight_percent") or Decimal("0"))
                + (cleaned.get("homework_weight_percent") or Decimal("0"))
                + (cleaned.get("semester_exam_weight_percent") or Decimal("0"))
            )
            if total != Decimal("100"):
                self.add_error(
                    None,
                    _("Active evaluation weights must sum to 100 (current total: %(total)s).")
                    % {"total": total},
                )
            if not cleaned.get("overall_min_percent"):
                self.add_error("overall_min_percent", _("Overall minimum is required for an active configuration."))
        return cleaned
