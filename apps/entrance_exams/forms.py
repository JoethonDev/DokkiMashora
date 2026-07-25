from django import forms
from django.forms import inlineformset_factory
from django.utils.translation import gettext_lazy as _

from apps.academics.models import Cohort, Diploma

from apps.core.forms import StyledFormMixin
from .models import EntranceChoice, EntranceExam, EntranceQuestion


class EntranceExamForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = EntranceExam
        fields = (
            "title",
            "description",
            "diploma",
            "cohort",
            "duration_minutes",
            "pass_percentage",
            "open_at",
            "close_at",
            "auto_send_results",
        )
        widgets = {
            "title": forms.TextInput(attrs={}),
            "description": forms.Textarea(attrs={}),
            "diploma": forms.Select(attrs={}),
            "cohort": forms.Select(attrs={}),
            "duration_minutes": forms.NumberInput(attrs={}),
            "pass_percentage": forms.NumberInput(attrs={}),
            "open_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "close_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
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


class EntranceQuestionForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = EntranceQuestion
        fields = ("text", "question_type", "max_score", "rubric", "order")
        widgets = {
            "text": forms.Textarea(attrs={"rows": 4}),
            "question_type": forms.Select(attrs={}),
            "max_score": forms.NumberInput(attrs={}),
            "rubric": forms.Textarea(attrs={"rows": 3}),
            "order": forms.NumberInput(attrs={}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["text"].label = _("Question text")
        self.fields["order"].help_text = _("Display order (lower = first)")


class EntranceChoiceForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = EntranceChoice
        fields = ("text", "score", "order")
        widgets = {
            "text": forms.TextInput(attrs={"placeholder": _("Choice text")}),
            "score": forms.NumberInput(attrs={}),
            "order": forms.NumberInput(attrs={}),
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


class EntranceGradeForm(StyledFormMixin, forms.Form):
    score = forms.DecimalField(
        label=_("Score"),
        max_digits=5,
        decimal_places=2,
        required=True,
        widget=forms.NumberInput(attrs={}),
    )
    feedback = forms.CharField(
        label=_("Feedback"),
        widget=forms.Textarea(attrs={}),
        required=False,
    )
