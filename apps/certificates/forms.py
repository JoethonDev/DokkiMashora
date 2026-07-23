from django import forms
from django.utils.translation import gettext_lazy as _

from .models import CertificateTemplate


class CertificateTemplateForm(forms.ModelForm):
    class Meta:
        model = CertificateTemplate
        fields = [
            "name", "diploma", "language", "template_type",
            "body_html", "active", "is_placeholder",
        ]
        widgets = {
            "body_html": forms.Textarea(attrs={"rows": 10, "class": ""}),
        }
        help_texts = {
            "body_html": _(
                "Use Django template variables: "
                "{{ student_name }}, {{ diploma_title }}, {{ cohort_name }}, "
                "{{ completion_date }}, {{ certificate_number }}, {{ issue_date }}"
            ),
        }
