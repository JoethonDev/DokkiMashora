from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms import StyledFormMixin
from .models import CertificateTemplate


class CertificateTemplateForm(StyledFormMixin, forms.ModelForm):
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
            "body_html": _(
                "Use Django template variables: "
                "{{ student_name }}, {{ diploma_title }}, {{ cohort_name }}, "
                "{{ completion_date }}, {{ certificate_number }}, {{ issue_date }}"
            ),
        }
