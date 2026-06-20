from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .constants import MAX_FILE_SIZE_BYTES


class PaymentUploadForm(forms.Form):
    csv_file = forms.FileField(
        label=_("CSV file"),
        help_text=_("Upload a CSV file with columns: national_id, amount, payment_date"),
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
