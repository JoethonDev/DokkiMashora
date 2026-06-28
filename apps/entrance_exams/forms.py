from django import forms
from django.forms import inlineformset_factory
from django.utils.translation import gettext_lazy as _

from apps.academics.models import Cohort, Diploma

from .models import EntranceChoice, EntranceExam, EntranceQuestion


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
            "cohort": forms.Select(attrs={"class": "dm-input w-full rounded-lg px-3 py-2 text-sm shadow-sm"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["diploma"].queryset = Diploma.objects.all()
        cohort_qs = Cohort.objects.none()
        cohort_val = self.data.get("cohort") or self.initial.get("cohort")
        if cohort_val:
            cohort_qs = Cohort.objects.filter(pk=cohort_val)
        self.fields["cohort"].queryset = cohort_qs
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


class EntranceQuestionForm(forms.ModelForm):
    class Meta:
        model = EntranceQuestion
        fields = ("text", "question_type", "points", "rubric", "order")
        widgets = {
            "text": forms.Textarea(attrs={"rows": 4}),
            "rubric": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["text"].label = _("Question text")
        self.fields["order"].help_text = _("Display order (lower = first)")


class EntranceChoiceForm(forms.ModelForm):
    class Meta:
        model = EntranceChoice
        fields = ("text", "score", "order")
        widgets = {
            "text": forms.TextInput(attrs={"placeholder": _("Choice text")}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["score"].label = _("Score")
        self.fields["order"].label = _("Order")


EntranceChoiceFormSet = inlineformset_factory(
    EntranceQuestion,
    EntranceChoice,
    form=EntranceChoiceForm,
    extra=2,
    can_delete=True,
)


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
