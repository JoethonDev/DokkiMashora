from django import forms
from django.utils.translation import gettext_lazy as _

from apps.academics.models import Cohort, Diploma

from .models import EntranceExam


class EntranceExamForm(forms.ModelForm):
    class Meta:
        model = EntranceExam
        fields = (
            "title",
            "description",
            "diploma",
            "cohort",
            "duration_minutes",
            "passing_score",
            "open_at",
            "close_at",
        )
        widgets = {
            "open_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "close_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["diploma"].queryset = Diploma.objects.all()
        self.fields["cohort"].queryset = Cohort.objects.select_related("diploma").all()
        self.fields["diploma"].empty_label = _("— Select —")
        self.fields["cohort"].empty_label = _("— Select —")

    def clean(self):
        cleaned_data = super().clean()
        diploma = cleaned_data.get("diploma")
        cohort = cleaned_data.get("cohort")
        if diploma and cohort and cohort.diploma_id != diploma.pk:
            raise forms.ValidationError(_("Selected cohort does not belong to the selected diploma."))
        open_at = cleaned_data.get("open_at")
        close_at = cleaned_data.get("close_at")
        if open_at and close_at and open_at >= close_at:
            raise forms.ValidationError(_("Open time must be before close time."))
        return cleaned_data


class EntranceGradeForm(forms.Form):
    score = forms.DecimalField(
        label=_("Score"),
        max_digits=5,
        decimal_places=2,
        required=True,
    )
    feedback = forms.CharField(
        label=_("Feedback"),
        widget=forms.Textarea,
        required=False,
    )
