from django import forms
from django.utils.translation import gettext_lazy as _

from apps.academics.models import Course, Diploma
from apps.core.forms import StyledFormMixin

from .design import (
    HEX_COLOR_RE,
    design_text_defaults,
    normalize_design,
    validate_logo_bytes,
)
from .models import (
    CERTIFICATE_BORDER_CHOICES,
    CERTIFICATE_DESIGN_DEFAULTS,
    CERTIFICATE_LAYOUT_CHOICES,
    CERTIFICATE_LOGO_SIZE_CHOICES,
    CERTIFICATE_TEXT_ALIGN_CHOICES,
    CERTIFICATE_TEXT_SIZE_CHOICES,
    CertificateTemplate,
)
from .source_adapters import validate_source_format, validate_source_size

_DESIGN_FIELD_NAMES = (
    "layout", "accent_color", "background_color", "border_style", "text_size",
    "text_align", "title_text", "intro_text", "closing_text", "show_program",
    "show_certificate_number", "show_completion_date", "show_issue_date",
    "show_cohort", "show_signature", "signature_name", "signature_title",
    "footer_note", "logo", "remove_logo", "logo_size",
)


class CertificateTemplateForm(StyledFormMixin, forms.ModelForm):
    program = forms.ChoiceField(
        label=_("Program"),
        choices=[],
        help_text=_("Select the diploma or course this template belongs to."),
    )
    source_file = forms.FileField(
        label=_("Certificate source"),
        required=False,
        help_text=_(
            "Optional PDF, PSD, or JPG/JPEG certificate artwork. "
            "Uploaded artwork takes precedence over this HTML design."
        ),
        widget=forms.ClearableFileInput(attrs={
            "accept": ".pdf,.psd,.jpg,.jpeg,application/pdf,image/vnd.adobe.photoshop,image/jpeg",
        }),
    )
    logo = forms.FileField(
        label=_("Logo image"),
        required=False,
        help_text=_("PNG or JPG, up to 1 MB. The logo appears above the certificate title."),
        widget=forms.ClearableFileInput(attrs={"accept": ".png,.jpg,.jpeg,image/png,image/jpeg"}),
    )
    remove_logo = forms.BooleanField(
        label=_("Remove current logo"),
        required=False,
    )

    layout = forms.ChoiceField(label=_("Layout"), choices=CERTIFICATE_LAYOUT_CHOICES)
    accent_color = forms.CharField(
        label=_("Accent color"),
        max_length=7,
        widget=forms.TextInput(attrs={"type": "color"}),
        help_text=_("Used for the frame, rules, and title. Choose a dark color for readable text."),
    )
    background_color = forms.CharField(
        label=_("Background color"),
        max_length=7,
        widget=forms.TextInput(attrs={"type": "color"}),
        help_text=_("The certificate paper color."),
    )
    border_style = forms.ChoiceField(label=_("Border"), choices=CERTIFICATE_BORDER_CHOICES)
    text_size = forms.ChoiceField(label=_("Text size"), choices=CERTIFICATE_TEXT_SIZE_CHOICES)
    text_align = forms.ChoiceField(label=_("Text alignment"), choices=CERTIFICATE_TEXT_ALIGN_CHOICES)

    title_text = forms.CharField(
        label=_("Certificate title"),
        required=False,
        max_length=80,
    )
    intro_text = forms.CharField(
        label=_("Opening line"),
        required=False,
        max_length=160,
    )
    closing_text = forms.CharField(
        label=_("Closing line"),
        required=False,
        max_length=200,
    )

    show_program = forms.BooleanField(label=_("Program title"), required=False)
    show_certificate_number = forms.BooleanField(label=_("Certificate number"), required=False)
    show_completion_date = forms.BooleanField(label=_("Completion date"), required=False)
    show_issue_date = forms.BooleanField(label=_("Issue date"), required=False)
    show_cohort = forms.BooleanField(label=_("Cohort / version"), required=False)
    show_signature = forms.BooleanField(label=_("Signature block"), required=False)

    signature_name = forms.CharField(
        label=_("Signatory name"),
        required=False,
        max_length=80,
    )
    signature_title = forms.CharField(
        label=_("Signatory title"),
        required=False,
        max_length=120,
    )
    footer_note = forms.CharField(
        label=_("Footer note"),
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={}),
    )
    logo_size = forms.ChoiceField(label=_("Logo size"), choices=CERTIFICATE_LOGO_SIZE_CHOICES)

    _logo_bytes: bytes | None = None
    _logo_content_type: str = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        diploma_choices = [
            (f"diploma:{diploma.pk}", f"{_('Diploma')}: {diploma.title}")
            for diploma in Diploma.objects.order_by("title")
        ]
        course_choices = [
            (f"course:{course.pk}", f"{_('Course')}: {course.title}")
            for course in Course.objects.order_by("title")
        ]
        self.fields["program"].choices = [
            ("", _("Select a program")),
            *diploma_choices,
            *course_choices,
        ]

        design = self.instance.design if self.instance and self.instance.design else {}
        for name in (
            "layout", "accent_color", "background_color", "border_style",
            "text_size", "text_align", "logo_size",
        ):
            self.fields[name].initial = design.get(name, CERTIFICATE_DESIGN_DEFAULTS.get(name))
        for name in (
            "show_program", "show_certificate_number", "show_completion_date",
            "show_issue_date", "show_cohort", "show_signature",
        ):
            self.fields[name].initial = design.get(name, CERTIFICATE_DESIGN_DEFAULTS.get(name, False))
        for name in ("title_text", "intro_text", "closing_text", "signature_name", "signature_title", "footer_note"):
            if design.get(name):
                self.fields[name].initial = design[name]

        language = (
            self.data.get("language")
            or (self.instance.language if self.instance and self.instance.pk else "ar")
        )
        text_defaults = design_text_defaults(language)
        for name, default in text_defaults.items():
            self.fields[name].widget.attrs["placeholder"] = default
        self.fields["title_text"].help_text = _("Leave blank to use the default certificate title.")
        self.fields["intro_text"].help_text = _("Leave blank to use the default opening line.")
        self.fields["closing_text"].help_text = _("Leave blank to use the default closing line.")
        self.fields["signature_name"].help_text = _("Shown under the signature line when the signature block is enabled.")
        self.fields["signature_title"].help_text = _("Optional job title or role under the signatory name.")
        self.fields["footer_note"].help_text = _("Optional small note at the bottom of the certificate.")
        self.fields["is_placeholder"].label = _("Placeholder template")

        if self.instance and self.instance.pk:
            if self.instance.course_id:
                self.fields["program"].initial = f"course:{self.instance.course_id}"
            elif self.instance.diploma_id:
                self.fields["program"].initial = f"diploma:{self.instance.diploma_id}"
            # Existing templates have a dedicated source replacement workflow
            # below the model form. Keep the create form focused on the
            # one-step source upload without duplicating that control on edit.
            self.fields.pop("source_file", None)
        self.order_fields([
            name for name in (
                "name", "program", "language", "template_type", "active",
                "is_placeholder", "layout", "accent_color", "background_color",
                "border_style", "text_size", "text_align", "title_text",
                "intro_text", "closing_text", "show_program",
                "show_certificate_number", "show_completion_date", "show_issue_date",
                "show_cohort", "show_signature", "signature_name", "signature_title",
                "footer_note", "logo", "remove_logo", "logo_size", "source_file",
            )
            if name in self.fields
        ])

    def clean_program(self):
        value = self.cleaned_data.get("program", "")
        kind, _, raw_id = value.partition(":")
        try:
            object_id = int(raw_id)
        except (TypeError, ValueError):
            raise forms.ValidationError(_("Select a valid program.")) from None

        if kind == "diploma":
            try:
                diploma = Diploma.objects.get(pk=object_id)
            except Diploma.DoesNotExist:
                raise forms.ValidationError(_("Select a valid program.")) from None
            self.instance.diploma = diploma
            self.instance.course = None
        elif kind == "course":
            try:
                course = Course.objects.get(pk=object_id)
            except Course.DoesNotExist:
                raise forms.ValidationError(_("Select a valid program.")) from None
            self.instance.course = course
            self.instance.diploma = None
        else:
            raise forms.ValidationError(_("Select a valid program."))
        return value

    def clean_accent_color(self):
        value = (self.cleaned_data.get("accent_color") or "").strip()
        if not HEX_COLOR_RE.fullmatch(value):
            raise forms.ValidationError(_("Enter a valid color in #RRGGBB format."))
        return value.lower()

    def clean_background_color(self):
        value = (self.cleaned_data.get("background_color") or "").strip()
        if not HEX_COLOR_RE.fullmatch(value):
            raise forms.ValidationError(_("Enter a valid color in #RRGGBB format."))
        return value.lower()

    def clean_logo(self):
        logo = self.cleaned_data.get("logo")
        if not logo:
            return logo
        data = logo.read()
        logo.seek(0)
        try:
            content_type = validate_logo_bytes(data, logo.name)
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc
        self._logo_bytes = data
        self._logo_content_type = content_type
        return logo

    def _design_from(self, data) -> dict:
        return normalize_design({
            "layout": data.get("layout"),
            "accent_color": data.get("accent_color"),
            "background_color": data.get("background_color"),
            "border_style": data.get("border_style"),
            "text_size": data.get("text_size"),
            "text_align": data.get("text_align"),
            "logo_size": data.get("logo_size"),
            "show_program": bool(data.get("show_program")),
            "show_certificate_number": bool(data.get("show_certificate_number")),
            "show_completion_date": bool(data.get("show_completion_date")),
            "show_issue_date": bool(data.get("show_issue_date")),
            "show_cohort": bool(data.get("show_cohort")),
            "show_signature": bool(data.get("show_signature")),
            "title_text": data.get("title_text") or "",
            "intro_text": data.get("intro_text") or "",
            "closing_text": data.get("closing_text") or "",
            "signature_name": data.get("signature_name") or "",
            "signature_title": data.get("signature_title") or "",
            "footer_note": data.get("footer_note") or "",
        })

    def build_design(self) -> dict:
        return self._design_from(self.cleaned_data)

    def get_design(self) -> dict:
        """Current persisted/default design, used for the first server render."""
        return normalize_design(self.instance.design or {})

    def get_logo_payload(self) -> tuple[bytes | None, str]:
        return self._logo_bytes, self._logo_content_type

    def clean(self):
        cleaned = super().clean()
        # Let model validation see the visual design so a legacy body_html does
        # not block a save that is replacing it.
        self.instance.design = self._design_from(cleaned)
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.design = self.build_design()
        if commit:
            instance.save()
            self.save_m2m()
        return instance

    class Meta:
        model = CertificateTemplate
        fields = [
            "name", "language", "template_type", "active", "is_placeholder",
        ]
        widgets = {
            "name": forms.TextInput(attrs={}),
            "language": forms.Select(attrs={}),
            "template_type": forms.Select(attrs={}),
            "active": forms.CheckboxInput(attrs={}),
            "is_placeholder": forms.CheckboxInput(attrs={}),
        }


class CertificateTemplateDetailsForm(CertificateTemplateForm):
    """Admin template details for the artwork-required workflow.

    The visual HTML design is intentionally not exposed here. Existing design
    rows remain render fallbacks, but every template created or edited through
    this form requires uploaded artwork. The design JSON is left untouched.
    """

    _source_bytes: bytes | None = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in _DESIGN_FIELD_NAMES:
            self.fields.pop(name, None)
        if not (self.instance and self.instance.pk):
            self.fields["source_file"].required = True
            self.fields["source_file"].label = _("Certificate artwork")
            self.fields["source_file"].help_text = _(
                "Required. Upload the certificate as PDF, PSD, or JPG/JPEG artwork."
            )
        self.order_fields([
            name for name in (
                "name", "program", "language", "template_type", "active",
                "is_placeholder", "source_file",
            )
            if name in self.fields
        ])

    def clean_source_file(self):
        source = self.cleaned_data.get("source_file")
        if not source:
            return source
        data = source.read()
        source.seek(0)
        try:
            validate_source_size(data)
            validate_source_format(source.name, source.content_type or "")
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc
        self._source_bytes = data
        return source

    def get_source_payload(self) -> tuple[bytes | None, str, str]:
        source = self.cleaned_data.get("source_file")
        if source is None or self._source_bytes is None:
            return None, "", ""
        return self._source_bytes, source.name, source.content_type or ""

    def clean(self):
        # Skip the hidden designer's design assignment: this form must not
        # overwrite an existing stored design while editing artwork metadata.
        return forms.ModelForm.clean(self)

    def save(self, commit=True):
        instance = forms.ModelForm.save(self, commit=False)
        if commit:
            instance.save()
            self.save_m2m()
        return instance

    class Meta(CertificateTemplateForm.Meta):
        pass
