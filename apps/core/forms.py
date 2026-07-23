from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.core.models import LANDING_CONTENT_DEFAULTS, LandingPageSettings

CONTENT_SECTIONS = {
    "Hero": (
        "hero_eyebrow",
        "hero_title",
        "hero_body",
        "hero_primary",
        "hero_secondary",
        "announcement_title",
        "announcement_body",
    ),
    "Introduction": ("intro_eyebrow", "intro_title", "intro_body"),
    "Benefits": (
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
    "Parenting": ("parenting_eyebrow", "parenting_title", "parenting_body"),
    "Diploma": ("diploma_eyebrow", "diploma_title", "diploma_body"),
    "Programs": (
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
    "Quote": ("quote", "quote_name", "quote_role"),
    "Call to action": ("cta_eyebrow", "cta_title", "cta_body", "cta_label"),
    "Footer": (
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


class LandingPageSettingsForm(forms.ModelForm):
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
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for language, defaults in LANDING_CONTENT_DEFAULTS.items():
            for key, default in defaults.items():
                value = (
                    self.instance.content.get(language, {}).get(key, default) if self.instance.pk else default
                )
                self.fields[f"{key}_{language}"] = forms.CharField(
                    label=_("%(key)s (%(language)s)")
                    % {"key": key.replace("_", " ").title(), "language": language.upper()},
                    initial=value,
                    widget=forms.Textarea(attrs={"rows": 3})
                    if key.endswith(("body", "quote", "tagline"))
                    else forms.TextInput(),
                )
        for field in self.fields.values():
            field.widget.attrs.pop("class", None)

    def clean(self):
        cleaned = super().clean()
        for name in ("hero_image", "parenting_image", "diploma_image"):
            image = cleaned.get(name)
            if image and getattr(getattr(image, "image", None), "format", "") != "WEBP":
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
