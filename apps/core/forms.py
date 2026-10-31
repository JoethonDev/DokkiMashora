from urllib.parse import urlsplit

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.core.models import LANDING_CONTENT_DEFAULTS, LandingAnnouncement, LandingPageSettings

WIDGET_CLASSES = {
    "Select": "form-select",
    "SelectMultiple": "form-select",
    "NullBooleanSelect": "form-select",
    "Textarea": "form-textarea",
    "CheckboxInput": "form-checkbox",
    "RadioSelect": "form-radio",
    "CheckboxSelectMultiple": "form-checkbox",
    "FileInput": "form-file",
    "ClearableFileInput": "form-file",
}
DEFAULT_WIDGET_CLASS = "form-input"
CANONICAL_WIDGET_CLASSES = {"form-input", "form-select", "form-checkbox", "form-radio", "form-file"}


def canonical_widget_class(widget) -> str:
    """Return the canonical presentation class for a widget.

    Resolve through the widget's MRO so project subclasses keep their widget
    family's class: MultipleFileInput(ClearableFileInput) is a file control and
    must render as form-file, not fall back to the generic input class.
    """
    for widget_cls in type(widget).__mro__:
        canonical = WIDGET_CLASSES.get(widget_cls.__name__)
        if canonical:
            return canonical
    return DEFAULT_WIDGET_CLASS


class StyledFormMixin:
    """R1 layer 1: assigns the canonical widget class to every field.

    Must be the FIRST base class of every form in the project.
    Corrects wrong legacy classes (e.g. Select + form-input) and is idempotent.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            canonical = canonical_widget_class(widget)
            existing = set(widget.attrs.get("class", "").split()) - CANONICAL_WIDGET_CLASSES
            widget.attrs["class"] = " ".join(sorted(existing | {canonical}))


CONTENT_SECTIONS = {
    _("Hero"): (
        "hero_eyebrow",
        "hero_title",
        "hero_body",
        "hero_primary",
        "hero_secondary",
    ),
    _("Introduction"): ("intro_eyebrow", "intro_title", "intro_body"),
    _("Benefits"): (
        "benefits_eyebrow",
        "benefits_title",
        "benefits_body",
        "benefit_1_title",
        "benefit_1_body",
        "benefit_2_title",
        "benefit_2_body",
        "benefit_3_title",
        "benefit_3_body",
        "benefit_4_title",
        "benefit_4_body",
    ),
    _("Parenting"): ("parenting_eyebrow", "parenting_title", "parenting_body"),
    _("Diploma"): ("diploma_eyebrow", "diploma_title", "diploma_body"),
    _("Programs"): (
        "programs_eyebrow",
        "programs_title",
        "programs_body",
        "diplomas_eyebrow",
        "diplomas_title",
        "diplomas_body",
        "courses_eyebrow",
        "courses_title",
        "courses_body",
        "status_open",
        "status_closed",
        "success_groups",
        "diploma_courses_label",
        "current_edition",
        "entrance_note",
        "apply_now",
    ),
    _("Quote"): ("quote", "quote_name", "quote_role"),
    _("Call to action"): ("cta_eyebrow", "cta_title", "cta_body", "cta_label"),
    _("Footer"): (
        "footer_tagline",
        "footer_address",
        "footer_about_title",
        "footer_links_title",
        "footer_contact_title",
        "footer_follow_title",
        "footer_programs_link",
        "footer_apply_link",
        "footer_login_link",
        "footer_hours",
        "footer_email_placeholder",
        "footer_phone_placeholder",
        "footer_note",
        "stat_1",
        "stat_2",
        "stat_3",
        "stat_4",
    ),
}


class LandingPageSettingsForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = LandingPageSettings
        fields = (
            "hero_image",
            "parenting_image",
            "diploma_image",
            "show_live_stats",
            "stat_value_1",
            "stat_value_2",
            "stat_value_3",
            "stat_value_4",
            "contact_email",
            "contact_phone",
            "facebook_url",
            "instagram_url",
            "youtube_url",
        )
        widgets = {
            "hero_image": forms.FileInput(attrs={"accept": "image/webp"}),
            "parenting_image": forms.FileInput(attrs={"accept": "image/webp"}),
            "diploma_image": forms.FileInput(attrs={"accept": "image/webp"}),
            "show_live_stats": forms.CheckboxInput(),
            "stat_value_1": forms.TextInput(),
            "stat_value_2": forms.TextInput(),
            "stat_value_3": forms.TextInput(),
            "stat_value_4": forms.TextInput(),
            "contact_email": forms.EmailInput(),
            "contact_phone": forms.TextInput(),
            "facebook_url": forms.URLInput(),
            "instagram_url": forms.URLInput(),
            "youtube_url": forms.URLInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for language, defaults in LANDING_CONTENT_DEFAULTS.items():
            for key, default in defaults.items():
                value = (
                    self.instance.content.get(language, {}).get(key, default) if self.instance.pk else default
                )
                field = forms.CharField(
                    label=_("%(key)s (%(language)s)")
                    % {"key": key.replace("_", " ").title(), "language": language.upper()},
                    initial=value,
                    widget=forms.Textarea(attrs={"rows": 3})
                    if key.endswith(("body", "quote", "tagline"))
                    else forms.TextInput(),
                )
                self.fields[f"{key}_{language}"] = field
                widget = field.widget
                widget.attrs["class"] = canonical_widget_class(widget)

    def clean(self):
        cleaned = super().clean()
        for name in ("hero_image", "parenting_image", "diploma_image"):
            image = cleaned.get(name)
            uploaded = getattr(image, "image", None) if image else None
            if uploaded is not None and getattr(uploaded, "format", "") != "WEBP":
                self.add_error(name, ValidationError(_("Please upload a WebP image.")))
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.content = {
            language: {key: self.cleaned_data[f"{key}_{language}"] for key in defaults}
            for language, defaults in LANDING_CONTENT_DEFAULTS.items()
        }
        if commit:
            instance.save()
        return instance

    def content_sections(self):
        return [
            (section, [(self[f"{key}_en"], self[f"{key}_ar"]) for key in keys])
            for section, keys in CONTENT_SECTIONS.items()
        ]

    def image_fields(self):
        return [self[name] for name in ("hero_image", "parenting_image", "diploma_image")]

    def stat_fields(self):
        return [self[f"stat_value_{number}"] for number in range(1, 5)]

    def contact_fields(self):
        return [
            self[name]
            for name in ("contact_email", "contact_phone", "facebook_url", "instagram_url", "youtube_url")
        ]


class LandingAnnouncementForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = LandingAnnouncement
        fields = (
            "title_ar",
            "title_en",
            "message_ar",
            "message_en",
            "cta_label_ar",
            "cta_label_en",
            "cta_url",
            "starts_at",
            "ends_at",
            "is_active",
        )
        widgets = {
            "title_ar": forms.TextInput(),
            "title_en": forms.TextInput(),
            "message_ar": forms.Textarea(attrs={"rows": 3}),
            "message_en": forms.Textarea(attrs={"rows": 3}),
            "cta_label_ar": forms.TextInput(),
            "cta_label_en": forms.TextInput(),
            "cta_url": forms.TextInput(attrs={"placeholder": "/apply"}),
            "is_active": forms.CheckboxInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("starts_at", "ends_at"):
            field = self.fields[name]
            field.input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"]
            field.widget = forms.DateTimeInput(
                format="%Y-%m-%dT%H:%M",
                attrs={"type": "datetime-local", "class": "form-input"},
            )

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("title_ar") or not cleaned.get("title_en"):
            raise ValidationError(_("Enter the announcement title in both Arabic and English."))
        if not cleaned.get("message_ar") or not cleaned.get("message_en"):
            raise ValidationError(_("Enter the announcement message in both Arabic and English."))
        starts_at = cleaned.get("starts_at")
        ends_at = cleaned.get("ends_at")
        if starts_at and ends_at and ends_at <= starts_at:
            self.add_error("ends_at", _("The end time must be after the start time."))
        cta_label = cleaned.get("cta_label_ar") or cleaned.get("cta_label_en")
        cta_url = (cleaned.get("cta_url") or "").strip()
        if bool(cta_label) != bool(cta_url):
            raise ValidationError(_("Provide both a button label and a button URL, or leave both empty."))
        if cta_url:
            parsed = urlsplit(cta_url)
            if cta_url.startswith("//"):
                self.add_error("cta_url", _("Use an http(s) link or a site-relative path."))
            elif parsed.scheme and parsed.scheme.lower() not in {"http", "https"}:
                self.add_error("cta_url", _("Use an http(s) link or a site-relative path."))
            elif not parsed.scheme and not cta_url.startswith("/"):
                self.add_error("cta_url", _("Use an http(s) link or a site-relative path starting with /."))
        return cleaned
