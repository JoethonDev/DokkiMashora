"""Bounded settings for the no-code HTML certificate designer.

The server owns the certificate markup. This module only normalizes the small
set of design choices stored on ``CertificateTemplate.design`` and resolves the
translated default wording. It is intentionally free of rendering imports so
both forms and services can use it.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

from django.utils import translation
from django.utils.translation import gettext
from PIL import Image

from .models import (
    CERTIFICATE_BORDER_CHOICES,
    CERTIFICATE_DESIGN_DEFAULTS,
    CERTIFICATE_LAYOUT_CHOICES,
    CERTIFICATE_LOGO_SIZE_CHOICES,
    CERTIFICATE_TEXT_ALIGN_CHOICES,
    CERTIFICATE_TEXT_SIZE_CHOICES,
)

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

MAX_LOGO_BYTES = 1 * 1024 * 1024
MAX_LOGO_PIXELS = 4_000_000
LOGO_TYPES = {
    ".png": ("PNG", "image/png"),
    ".jpg": ("JPEG", "image/jpeg"),
    ".jpeg": ("JPEG", "image/jpeg"),
}

DESIGN_TEXT_DEFAULTS = (
    ("title_text", "Certificate of Completion"),
    ("intro_text", "The center certifies that"),
    ("closing_text", "has successfully completed the requirements of"),
)
DESIGN_TEXT_KEYS = tuple(key for key, _msgid in DESIGN_TEXT_DEFAULTS)
DESIGN_EXTRA_TEXT_KEYS = ("signature_name", "signature_title", "footer_note")
DESIGN_TEXT_MAX_LENGTHS = {
    "title_text": 80,
    "intro_text": 160,
    "closing_text": 200,
    "signature_name": 80,
    "signature_title": 120,
    "footer_note": 200,
}


def normalize_language(language: str | None) -> str:
    return "en" if language == "en" else "ar"


def design_text_defaults(language: str | None) -> dict[str, str]:
    """Translated default wording for the designer input placeholders."""
    with translation.override(normalize_language(language)):
        return {key: gettext(msgid) for key, msgid in DESIGN_TEXT_DEFAULTS}


def _choice(value, choices, fallback: str) -> str:
    allowed = {key for key, _label in choices}
    return value if value in allowed else fallback


def _bool(value, fallback: bool) -> bool:
    return value if isinstance(value, bool) else fallback


def _text(value, max_length: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_length]


def normalize_design(raw) -> dict:
    """Return a render-safe design mapping with only known keys."""
    raw = raw if isinstance(raw, dict) else {}
    defaults = CERTIFICATE_DESIGN_DEFAULTS
    design = {
        "version": 1,
        "layout": _choice(raw.get("layout"), CERTIFICATE_LAYOUT_CHOICES, defaults["layout"]),
        "accent_color": (
            raw.get("accent_color")
            if isinstance(raw.get("accent_color"), str)
            and HEX_COLOR_RE.fullmatch(raw["accent_color"])
            else defaults["accent_color"]
        ),
        "background_color": (
            raw.get("background_color")
            if isinstance(raw.get("background_color"), str)
            and HEX_COLOR_RE.fullmatch(raw["background_color"])
            else defaults["background_color"]
        ),
        "border_style": _choice(
            raw.get("border_style"), CERTIFICATE_BORDER_CHOICES, defaults["border_style"],
        ),
        "text_size": _choice(
            raw.get("text_size"), CERTIFICATE_TEXT_SIZE_CHOICES, defaults["text_size"],
        ),
        "text_align": _choice(
            raw.get("text_align"), CERTIFICATE_TEXT_ALIGN_CHOICES, defaults["text_align"],
        ),
        "show_program": _bool(raw.get("show_program"), defaults["show_program"]),
        "show_certificate_number": _bool(
            raw.get("show_certificate_number"), defaults["show_certificate_number"],
        ),
        "show_completion_date": _bool(
            raw.get("show_completion_date"), defaults["show_completion_date"],
        ),
        "show_issue_date": _bool(raw.get("show_issue_date"), defaults["show_issue_date"]),
        "show_cohort": _bool(raw.get("show_cohort"), defaults["show_cohort"]),
        "show_signature": _bool(raw.get("show_signature"), defaults["show_signature"]),
        "logo_size": _choice(raw.get("logo_size"), CERTIFICATE_LOGO_SIZE_CHOICES, defaults["logo_size"]),
    }
    for key in (*DESIGN_TEXT_KEYS, *DESIGN_EXTRA_TEXT_KEYS):
        design[key] = _text(raw.get(key), DESIGN_TEXT_MAX_LENGTHS[key])
    return design


def validate_logo_bytes(data: bytes, filename: str = "") -> str:
    """Validate an uploaded logo and return its safe MIME type.

    User-friendly form validation and the storage-side invariant both call this
    narrow helper so a logo cannot bypass image checks.
    """
    if not data:
        raise ValueError(gettext("The logo file is empty."))
    if len(data) > MAX_LOGO_BYTES:
        raise ValueError(gettext("The logo must be 1 MB or smaller."))
    try:
        image = Image.open(io.BytesIO(data))
        image.verify()
        image = Image.open(io.BytesIO(data))
        width, height = image.size
        image_format = image.format
    except Exception as exc:
        raise ValueError(gettext("The logo image is damaged or unsupported.")) from exc
    if image_format not in {fmt for fmt, _mime in LOGO_TYPES.values()}:
        raise ValueError(gettext("Upload a PNG or JPG logo."))
    if filename and not logo_extension_matches(filename, image_format):
        raise ValueError(gettext("The logo file type does not match its extension."))
    if width * height > MAX_LOGO_PIXELS:
        raise ValueError(gettext("The logo dimensions are too large."))
    mime_type = next(
        mime for fmt, mime in LOGO_TYPES.values() if fmt == image_format
    )
    return mime_type


def logo_extension_matches(filename: str, image_format: str) -> bool:
    expected = LOGO_TYPES.get(Path(filename or "").suffix.lower())
    return bool(expected and expected[0] == image_format)


def design_ink_colors(background_color: str, accent_color: str) -> tuple[str, str, str]:
    """Pick readable ink/muted heading colors for the chosen background."""

    def _rgb(hex_color: str) -> tuple[int, int, int]:
        return tuple(int(hex_color[index:index + 2], 16) for index in (1, 3, 5))

    def _luminance(hex_color: str) -> float:
        red, green, blue = (channel / 255 for channel in _rgb(hex_color))
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue

    dark_background = _luminance(background_color) < 0.45
    ink = "#FFFFFF" if dark_background else "#111827"
    muted = "#E5E7EB" if dark_background else "#6B7280"
    accent_luminance = _luminance(accent_color)
    if dark_background:
        heading = accent_color if accent_luminance > 0.55 else "#FFFFFF"
    else:
        heading = accent_color if accent_luminance < 0.72 else ink
    return ink, muted, heading
