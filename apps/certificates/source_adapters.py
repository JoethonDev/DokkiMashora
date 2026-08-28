"""Certificate source adapters (P7-02).

Pipeline contract (format-neutral):

    protected template source
      -> supported preview (PNG bytes + page/width/height metadata)
      -> validated field mapping
      -> generated output (implemented by P7-03 generation)

PDF uses PyMuPDF (fitz): pages are rendered to PNG previews and field placement
overlays text directly onto the source page. Raster sources (PSD and JPG/JPEG)
use PIL: the flattened composite is rendered to a PNG preview and PIL draws
placed fields. The formats intentionally use different adapters; there is no
common conversion.

Rendering rules are shared: preview-generate and final export both call
``overlay_text_on_source`` so there is no duplicated rendering logic. Text is
reshaped + bidi-ordered for RTL/Arabic when the optional arabic-reshaper and
python-bidi packages are available (guarded import; Latin-only rendering never
breaks), and the selected bundled font (Arabic-capable Cairo or Latin Inter) is
embedded rather than a base-14 placeholder.

All work here is pure/bounded and must run OUTSIDE DB transactions.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from django.utils.translation import gettext_lazy as _

from .models import (
    ALLOWED_MAPPING_KEYS,
    MAPPING_SCHEMA_VERSION,
    TEMPLATE_SOURCE_FORMAT_JPG,
    TEMPLATE_SOURCE_FORMAT_PDF,
    TEMPLATE_SOURCE_FORMAT_PSD,
)

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except (ImportError, OSError):  # optional: Latin-only rendering still works
    arabic_reshaper = None
    get_display = None
    _ARABIC_TEXT_SUPPORT = False
else:
    _ARABIC_TEXT_SUPPORT = True

logger = logging.getLogger(__name__)

_MAX_SOURCE_BYTES = 25 * 1024 * 1024  # 25 MB
_PREVIEW_DPI = 120
_PREVIEW_PIXEL_TO_PDF_POINT = 72 / _PREVIEW_DPI
# PyMuPDF receives a baseline point, while the browser editor positions the
# visible field box from its top edge.
_PDF_BASELINE_OFFSET = 0.7
_MAX_PREVIEW_BYTES = 8 * 1024 * 1024  # 8 MB
_IMAGE_PREVIEW_WIDTH = 1400  # raster preview/design reference width

_FONTS_DIR = Path(__file__).resolve().parent / "fonts"

# Default bundled font; Arabic-capable so both Arabic and Latin render.
DEFAULT_FONT_FAMILY = "cairo"

# font_family -> (bundled TTF filename, UI label). Renders embed the TTF.
FONT_REGISTRY = {
    "cairo": ("Cairo-400.ttf", "Cairo"),
    "cairo-bold": ("Cairo-700.ttf", "Cairo Bold"),
    "inter": ("Inter-400.ttf", "Inter"),
    "inter-bold": ("Inter-700.ttf", "Inter Bold"),
}

# Legacy generic families accepted by the v1 editor map to the default bundled
# font so stored v1 mappings keep rendering without an explicit font.
ALLOWED_FONT_FAMILIES = frozenset(FONT_REGISTRY) | {"", "serif", "sans-serif", "monospace"}

# Arabic script code points (incl. presentation forms) for auto-detection.
_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")

_fitz_font_cache: dict = {}


@dataclass
class SourcePreview:
    """Supported preview representation + server-known metadata."""

    preview_bytes: bytes
    width: int
    height: int
    page_count: int
    content_type: str = "image/png"


@dataclass
class FieldPlacement:
    """One validated dynamic-field placement (P7-03 schema v2).

    Coordinates are normalized fractions of the page (0..1) so they are
    resolution-independent; ``font_size`` is in preview pixels at the design
    preview DPI and ``align``
    is left/center/right; ``direction`` is ltr/rtl. ``width``/``height`` define
    the field box (normalized 0..1). v1 mappings without width/height default
    to 0.0 and are rendered as a zero-size point box (legacy placement).
    """

    page: int
    x: float
    y: float
    font_size: float
    font_family: str
    align: str
    direction: str
    width: float = 0.0
    height: float = 0.0


def validate_source_format(filename: str, content_type: str) -> str:
    """Return the detected source format or raise ValueError.

    Certificate artwork uploads accept PDF and PSD. The extension is checked
    as well as the browser-provided content type so renamed files cannot
    bypass format validation.
    """
    name = (filename or "").lower()
    content_type = (content_type or "").lower()
    if name.endswith(".pdf") or content_type == "application/pdf":
        return TEMPLATE_SOURCE_FORMAT_PDF
    if (
        name.endswith(".psd")
        or content_type in {"image/vnd.adobe.photoshop", "application/vnd.adobe.photoshop"}
    ):
        return TEMPLATE_SOURCE_FORMAT_PSD
    if name.endswith((".jpg", ".jpeg")) or content_type == "image/jpeg":
        return TEMPLATE_SOURCE_FORMAT_JPG
    raise ValueError(_("Unsupported certificate source. Upload a PDF, PSD, or JPG/JPEG file."))


def validate_source_size(data: bytes) -> None:
    if not data:
        raise ValueError(_("Uploaded certificate source is empty."))
    if len(data) > _MAX_SOURCE_BYTES:
        raise ValueError(_("Certificate source exceeds the maximum allowed size."))


def source_checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fitz_preview(data: bytes) -> SourcePreview:
    import fitz  # PyMuPDF

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        if doc.page_count < 1:
            raise ValueError(_("PDF source has no pages."))
        page = doc[0]
        pix = page.get_pixmap(dpi=_PREVIEW_DPI)
        png = pix.tobytes("png")
        if len(png) > _MAX_PREVIEW_BYTES:
            raise ValueError(_("PDF preview is too large."))
        return SourcePreview(
            preview_bytes=png,
            width=pix.width,
            height=pix.height,
            page_count=doc.page_count,
        )
    finally:
        doc.close()


def _raster_to_preview(image, label: str) -> SourcePreview:
    """Resize a PIL raster to the design reference width and PNG-encode it."""
    from PIL import Image

    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGB")
    target_width = _IMAGE_PREVIEW_WIDTH
    ratio = target_width / image.width
    target_height = max(1, int(image.height * ratio))
    image = image.resize((target_width, target_height), Image.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    png = buf.getvalue()
    if len(png) > _MAX_PREVIEW_BYTES:
        raise ValueError(_("{label} preview is too large.").format(label=label))
    return SourcePreview(
        preview_bytes=png,
        width=target_width,
        height=target_height,
        page_count=1,
    )


def _psd_preview(data: bytes) -> SourcePreview:
    from psd_tools import PSDImage

    psd = PSDImage.open(io.BytesIO(data))
    return _raster_to_preview(psd.composite(), _("PSD"))


def _image_preview(data: bytes) -> SourcePreview:
    from PIL import Image

    image = Image.open(io.BytesIO(data)).convert("RGB")
    return _raster_to_preview(image, _("Image"))


def render_source_preview(data: bytes, source_format: str) -> SourcePreview:
    """Render a supported preview for a source; outside DB transactions."""
    if source_format == TEMPLATE_SOURCE_FORMAT_PDF:
        return _fitz_preview(data)
    if source_format == TEMPLATE_SOURCE_FORMAT_PSD:
        return _psd_preview(data)
    if source_format == TEMPLATE_SOURCE_FORMAT_JPG:
        return _image_preview(data)
    raise ValueError(_("Unsupported certificate source format."))


def validate_field_mapping(mapping: dict, preview: SourcePreview) -> dict:
    """Validate a placement mapping against server-known preview metadata.

    Returns the normalized mapping (schema v2). Only allowlisted keys are
    accepted; page numbers must exist in the source; coordinates/align/
    direction/font/box size are range-checked. v1 mappings without width/height
    are accepted and normalized with width/height = 0.0 (point placement).
    Raises ValueError on any invalid placement.
    """
    if not isinstance(mapping, dict):
        raise ValueError(_("Field mapping must be an object."))
    allowed_aligns = {"left", "center", "right"}
    allowed_directions = {"ltr", "rtl"}

    normalized: dict = {}
    for key, placement in mapping.items():
        if key not in ALLOWED_MAPPING_KEYS:
            raise ValueError(_("Unknown mapping key: {key}").format(key=key))
        if not isinstance(placement, dict):
            raise ValueError(_("Placement for {key} must be an object.").format(key=key))

        page = int(placement.get("page", 1))
        if page < 1 or page > preview.page_count:
            raise ValueError(_("Placement page is out of range."))

        x = float(placement.get("x", 0))
        y = float(placement.get("y", 0))
        font_size = float(placement.get("font_size", 24))
        align = str(placement.get("align", "center"))
        direction = str(placement.get("direction", "rtl"))
        font_family = str(placement.get("font_family", ""))
        width = float(placement.get("width", 0.0))
        height = float(placement.get("height", 0.0))

        if not (0 <= x <= 1):
            raise ValueError(_("Placement x must be between 0 and 1."))
        if not (0 <= y <= 1):
            raise ValueError(_("Placement y must be between 0 and 1."))
        if not (0 <= width <= 1):
            raise ValueError(_("Placement width must be between 0 and 1."))
        if not (0 <= height <= 1):
            raise ValueError(_("Placement height must be between 0 and 1."))
        if not (4 <= font_size <= 120):
            raise ValueError(_("Placement font size must be between 4 and 120."))
        if align not in allowed_aligns:
            raise ValueError(_("Placement align must be left, center, or right."))
        if direction not in allowed_directions:
            raise ValueError(_("Placement direction must be ltr or rtl."))
        if font_family not in ALLOWED_FONT_FAMILIES:
            raise ValueError(_("Unsupported font family."))

        normalized[key] = FieldPlacement(
            page=page,
            x=x,
            y=y,
            font_size=font_size,
            font_family=font_family,
            align=align,
            direction=direction,
            width=width,
            height=height,
        ).__dict__

    return {"version": MAPPING_SCHEMA_VERSION, "fields": normalized}


# ---------------------------------------------------------------------------
# Shared rendering helpers (used by BOTH preview-generate and final export)
# ---------------------------------------------------------------------------


def _contains_arabic(text: str) -> bool:
    return bool(_ARABIC_RE.search(text))


def _shaped_display_text(text: str, direction: str) -> str:
    """Reshape + bidi-order text for visual rendering (Arabic-capable)."""
    if not text:
        return text
    if direction == "rtl" or _contains_arabic(text):
        if _ARABIC_TEXT_SUPPORT:
            return get_display(arabic_reshaper.reshape(text))
        return text  # packages unavailable: render unshaped (best effort)
    return text


def _wrap_text(text: str, measure, max_width: float, max_lines: int) -> list[str]:
    """Best-effort word wrap into at most max_lines lines (bounded).

    ``measure`` returns the rendered width of a candidate line. Short
    certificate fields typically stay on a single line.
    """
    text = text.strip()
    if not text:
        return []
    if max_width <= 0 or max_lines <= 1 or measure(text) <= max_width:
        return [text]
    lines: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = word if not current else current + " " + word
        if measure(candidate) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines[:max_lines]


def _resolve_font_file(font_family: str) -> Path:
    family = font_family or DEFAULT_FONT_FAMILY
    filename = FONT_REGISTRY.get(family, FONT_REGISTRY[DEFAULT_FONT_FAMILY])[0]
    return _FONTS_DIR / filename


def _pdf_font(font_family: str):
    """Resolve a mapping family to (fontname, fontfile, fitz.Font|None).

    Falls back to base-14 ``helv`` when the bundled font is unavailable so
    Latin-only rendering never breaks.
    """
    import fitz

    family = font_family or DEFAULT_FONT_FAMILY
    if family not in FONT_REGISTRY:
        family = DEFAULT_FONT_FAMILY
    fontname = "cai_" + family.replace("-", "_")
    if family in _fitz_font_cache:
        font = _fitz_font_cache[family]
        if font is None:
            return "helv", None, None
        return fontname, str(_resolve_font_file(family)), font
    try:
        font = fitz.Font(fontname=fontname, fontfile=str(_resolve_font_file(family)))
    except Exception:
        logger.exception("Failed to load certificate font %s; using helv", family)
        _fitz_font_cache[family] = None
        return "helv", None, None
    _fitz_font_cache[family] = font
    return fontname, str(_resolve_font_file(family)), font


def _overlay_on_pdf(data: bytes, mapping_fields: dict, field_values: dict[str, str]) -> bytes:
    """Embed selected fonts and place fields directly on PDF source pages."""
    import fitz  # PyMuPDF

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        registered: dict[int, set] = {}
        for key, value in field_values.items():
            placement = mapping_fields.get(key)
            if placement is None:
                continue
            page_number = placement["page"] - 1
            if page_number < 0 or page_number >= doc.page_count:
                continue
            page = doc[page_number]
            fontname, font_file, font = _pdf_font(placement.get("font_family") or "")
            if font_file and fontname not in registered.setdefault(page_number, set()):
                page.insert_font(fontname=fontname, fontfile=font_file)
                registered[page_number].add(fontname)

            # The editor stores a pixel size relative to the 120-DPI preview.
            # Convert it to PDF points so the generated PDF has the same
            # apparent size as the saved editor layout.
            font_size = float(placement["font_size"]) * _PREVIEW_PIXEL_TO_PDF_POINT
            rect = page.rect
            x0 = rect.width * placement["x"]
            y0 = rect.height * placement["y"]
            box_w = rect.width * float(placement.get("width", 0.0))
            box_h = rect.height * float(placement.get("height", 0.0))
            line_advance = font_size * 1.25
            max_lines = max(1, int(box_h / line_advance)) if box_h > 0 else 1

            def measure(s: str, font=font, fontname=fontname, font_size=font_size) -> float:
                if font is None:
                    return fitz.get_text_length(s, fontname=fontname, fontsize=font_size)
                return font.text_length(s, fontsize=font_size)

            if box_w > 0:
                lines = _wrap_text(value, measure, box_w, max_lines)
            else:
                lines = [value]
            align = placement.get("align", "center")
            direction = placement.get("direction", "rtl")

            draw_y = y0 + (font_size * _PDF_BASELINE_OFFSET)
            for line in lines:
                display = _shaped_display_text(line, direction)
                line_w = measure(display)
                if box_w > 0:
                    if align == "center":
                        x = x0 + (box_w - line_w) / 2
                    elif align == "right":
                        x = x0 + box_w - line_w
                    else:
                        x = x0
                else:
                    x = x0
                page.insert_text(
                    fitz.Point(x, draw_y),
                    display,
                    fontsize=font_size,
                    fontname=fontname,
                    fontfile=font_file,
                )
                draw_y += line_advance
        out = io.BytesIO()
        doc.save(out, garbage=3, deflate=True)
        return out.getvalue()
    finally:
        doc.close()


def _overlay_on_image(image, mapping_fields: dict, field_values: dict[str, str]) -> bytes:
    """Place fields on a PIL raster (legacy PSD composites and JPG/JPEG).

    Font sizes are scaled from the design reference width (1400) to the export
    resolution so preview and final export are proportionally identical.
    """
    from PIL import ImageDraw, ImageFont

    draw = ImageDraw.Draw(image)
    scale = image.width / _IMAGE_PREVIEW_WIDTH
    for key, value in field_values.items():
        placement = mapping_fields.get(key)
        if placement is None:
            continue
        font_px = max(1, int(round(float(placement["font_size"]) * scale)))
        try:
            font = ImageFont.truetype(str(_resolve_font_file(placement.get("font_family") or "")), font_px)
        except OSError:
            font = ImageFont.load_default()

        align = placement.get("align", "center")
        direction = placement.get("direction", "rtl")
        box_w = image.width * float(placement.get("width", 0.0))
        box_h = image.height * float(placement.get("height", 0.0))
        line_advance = font_px * 1.25
        max_lines = max(1, int(box_h / line_advance)) if box_h > 0 else 1

        def measure(s: str, font=font) -> float:
            return draw.textlength(s, font=font)

        if box_w > 0:
            lines = _wrap_text(value, measure, box_w, max_lines)
        else:
            lines = [value]
        x0 = image.width * placement["x"]
        y0 = image.height * placement["y"]

        y = y0
        for line in lines:
            display = _shaped_display_text(line, direction)
            line_w = measure(display)
            if box_w > 0:
                if align == "center":
                    x = x0 + (box_w - line_w) / 2
                elif align == "right":
                    x = x0 + box_w - line_w
                else:
                    x = x0
            else:
                x = x0
            draw.text((x, y), display, fill="black", font=font)
            y += line_advance

    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def overlay_text_on_source(
    data: bytes,
    source_format: str,
    mapping: dict,
    field_values: dict[str, str],
) -> bytes:
    """Render a generated output by overlaying allowlisted fields.

    PDF: PyMuPDF embeds the selected font and inserts text on the chosen page.
    PSD (legacy rows) / JPG/JPEG: PIL draws text on the flattened source,
    exported as PNG. Preview-generate and final export share this single path.
    Runs outside DB transactions; bounded by source size limits.
    """
    mapping_fields = (mapping or {}).get("fields", {})
    if source_format == TEMPLATE_SOURCE_FORMAT_PDF:
        return _overlay_on_pdf(data, mapping_fields, field_values)

    if source_format == TEMPLATE_SOURCE_FORMAT_PSD:
        from PIL import Image
        from psd_tools import PSDImage

        psd = PSDImage.open(io.BytesIO(data))
        return _overlay_on_image(psd.composite().convert("RGB"), mapping_fields, field_values)

    if source_format == TEMPLATE_SOURCE_FORMAT_JPG:
        from PIL import Image

        image = Image.open(io.BytesIO(data)).convert("RGB")
        return _overlay_on_image(image, mapping_fields, field_values)

    raise ValueError(_("Unsupported certificate source format."))
