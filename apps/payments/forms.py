from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.core.forms import StyledFormMixin
from .constants import MAX_FILE_SIZE_BYTES
from .models import PaymentRecord


class PaymentUploadForm(StyledFormMixin, forms.Form):
    csv_file = forms.FileField(
        label=_("CSV file"),
        help_text=_("Cols: amount, payment_date + ID/email/phone"),
        widget=forms.FileInput(attrs={"accept": ".csv"}),
    )

    def clean_csv_file(self):
        file = self.cleaned_data.get("csv_file")
        if not file:
            raise ValidationError(_("No file provided"))
        if not file.name.endswith(".csv"):
            raise ValidationError(_("Only CSV files are accepted"))
        if file.size > MAX_FILE_SIZE_BYTES:
            raise ValidationError(
                _("File size exceeds the maximum of %(size)s MB")
                % {"size": MAX_FILE_SIZE_BYTES // (1024 * 1024)}
            )
        if file.size == 0:
            raise ValidationError(_("Uploaded file is empty"))
        return file


class PaymentRecordEditForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = PaymentRecord
        fields = ["amount", "currency", "status", "paid_at", "due_at", "external_reference", "notes"]
        widgets = {
            "amount": forms.NumberInput(attrs={}),
            "currency": forms.Select(attrs={}),
            "status": forms.Select(attrs={}),
            "paid_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "due_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "external_reference": forms.TextInput(attrs={}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
