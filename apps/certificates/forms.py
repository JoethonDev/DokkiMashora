from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms import StyledFormMixin

from .models import CertificateTemplate


class CertificateTemplateForm(StyledFormMixin, forms.ModelForm):
    source_file = forms.FileField(
        label=_("Certificate source"),
        required=False,
        help_text=_("Optional PDF or PSD certificate artwork. You can position all fields after upload."),
        widget=forms.ClearableFileInput(attrs={
            "accept": ".pdf,.psd,.jpg,.jpeg,application/pdf,image/vnd.adobe.photoshop,image/jpeg",
        }),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Existing templates have a dedicated source replacement workflow
        # below the model form. Keep the create form focused on the one-step
        # source upload without duplicating that control on edit.
        if self.instance and self.instance.pk:
            self.fields.pop("source_file", None)

    class Meta:
        model = CertificateTemplate
        fields = [
            "name", "diploma", "language", "template_type",
            "body_html", "active", "is_placeholder",
        ]
        widgets = {
            "name": forms.TextInput(attrs={}),
            "diploma": forms.Select(attrs={}),
            "language": forms.Select(attrs={}),
            "template_type": forms.Select(attrs={}),
            "body_html": forms.Textarea(attrs={"rows": 10}),
            "active": forms.CheckboxInput(attrs={}),
            "is_placeholder": forms.CheckboxInput(attrs={}),
        }
        help_texts = {
            "body_html": _("e.g., {{ student_name }}, {{ diploma_title }}"),
        }
