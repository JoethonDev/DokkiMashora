"""Certificate source adapters (P7-02).

Pipeline contract (format-neutral):

    protected template source
      -> supported preview (PNG bytes + page/width/height metadata)
      -> validated field mapping
      -> generated output (implemented by P7-03 generation)

PDF uses PyMuPDF (fitz): pages are rendered to PNG previews and field placement
overlays text directly onto the source page. PSD uses psd-tools: the flattened
composite is rendered to a PNG preview and PIL draws placed fields. The two
formats intentionally use different adapters; there is no common conversion.

All work here is pure/bounded and must run OUTSIDE DB transactions.
"""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

from .models import (
    ALLOWED_MAPPING_KEYS,
    MAPPING_SCHEMA_VERSION,
    TEMPLATE_SOURCE_FORMAT_PDF,
    TEMPLATE_SOURCE_FORMAT_PSD,
)

logger = logging.getLogger(__name__)

_MAX_SOURCE_BYTES = 25 * 1024 * 1024  # 25 MB
_PREVIEW_DPI = 120
_MAX_PREVIEW_BYTES = 8 * 1024 * 1024  # 8 MB


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
    """One validated dynamic-field placement (P7-02 schema v1).

    Coordinates are normalized fractions of the page (0..1) so they are
    resolution-independent; ``font_size`` is in preview points and ``align``
    is left/center/right; ``direction`` is ltr/rtl.
    """

    page: int
    x: float
    y: float
    font_size: float
    font_family: str
    align: str
    direction: str


def validate_source_format(filename: str, content_type: str) -> str:
    """Return the detected source format or raise ValueError."""
    name = (filename or "").lower()
    if name.endswith(".pdf") or content_type == "application/pdf":
        return TEMPLATE_SOURCE_FORMAT_PDF
    if name.endswith(".psd") or content_type == "image/vnd.adobe.photoshop":
        return TEMPLATE_SOURCE_FORMAT_PSD
    raise ValueError(_("Unsupported certificate source. Upload a PDF or PSD file."))


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


def _psd_preview(data: bytes) -> SourcePreview:
    from PIL import Image
    from psd_tools import PSDImage

    psd = PSDImage.open(io.BytesIO(data))
    composite = psd.composite()
    if composite.mode not in ("RGB", "RGBA"):
        composite = composite.convert("RGBA" if "A" in composite.getbands() else "RGB")
    target_width = 1400
    ratio = target_width / composite.width
    target_height = max(1, int(composite.height * ratio))
    composite = composite.resize((target_width, target_height), Image.LANCZOS)
    buf = io.BytesIO()
    composite.save(buf, format="PNG")
    png = buf.getvalue()
    if len(png) > _MAX_PREVIEW_BYTES:
        raise ValueError(_("PSD preview is too large."))
    return SourcePreview(
        preview_bytes=png,
        width=target_width,
        height=target_height,
        page_count=1,
    )


def render_source_preview(data: bytes, source_format: str) -> SourcePreview:
    """Render a supported preview for a source; outside DB transactions."""
    if source_format == TEMPLATE_SOURCE_FORMAT_PDF:
        return _fitz_preview(data)
    if source_format == TEMPLATE_SOURCE_FORMAT_PSD:
        return _psd_preview(data)
    raise ValueError(_("Unsupported certificate source format."))


def validate_field_mapping(mapping: dict, preview: SourcePreview) -> dict:
    """Validate a placement mapping against server-known preview metadata.

    Returns the normalized mapping. Only allowlisted keys are accepted; page
    numbers must exist in the source; coordinates/align/direction/font are
    range-checked. Raises ValueError on any invalid placement.
    """
    if not isinstance(mapping, dict):
        raise ValueError(_("Field mapping must be an object."))
    allowed_aligns = {"left", "center", "right"}
    allowed_directions = {"ltr", "rtl"}
    allowed_fonts = {"", "serif", "sans-serif", "monospace"}

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

        if not (0 <= x <= 1):
            raise ValueError(_("Placement x must be between 0 and 1."))
        if not (0 <= y <= 1):
            raise ValueError(_("Placement y must be between 0 and 1."))
        if not (4 <= font_size <= 120):
            raise ValueError(_("Placement font size must be between 4 and 120."))
        if align not in allowed_aligns:
            raise ValueError(_("Placement align must be left, center, or right."))
        if direction not in allowed_directions:
            raise ValueError(_("Placement direction must be ltr or rtl."))
        if font_family not in allowed_fonts:
            raise ValueError(_("Unsupported font family."))

        normalized[key] = FieldPlacement(
            page=page,
            x=x,
            y=y,
            font_size=font_size,
            font_family=font_family,
            align=align,
            direction=direction,
        ).__dict__

    return {"version": MAPPING_SCHEMA_VERSION, "fields": normalized}


def overlay_text_on_source(
    data: bytes,
    source_format: str,
    mapping: dict,
    field_values: dict[str, str],
) -> bytes:
    """Render a generated output by overlaying allowlisted fields.

    PDF: PyMuPDF inserts text directly on the selected page.
    PSD: psd-tools composite + PIL draws text, exported as PNG.
    Runs outside DB transactions; bounded by source size limits.
    """
    mapping_fields = (mapping or {}).get("fields", {})
    if source_format == TEMPLATE_SOURCE_FORMAT_PDF:
        import fitz  # PyMuPDF

        doc = fitz.open(stream=data, filetype="pdf")
        try:
            for key, value in field_values.items():
                placement = mapping_fields.get(key)
                if placement is None:
                    continue
                page = doc[placement["page"] - 1]
                rect = page.rect
                point = fitz.Point(
                    rect.width * placement["x"],
                    rect.height * placement["y"],
                )
                page.insert_text(
                    point,
                    value,
                    fontsize=placement["font_size"],
                    fontname=placement.get("font_family") or "helv",
                )
            out = io.BytesIO()
            doc.save(out, garbage=3, deflate=True)
            return out.getvalue()
        finally:
            doc.close()

    if source_format == TEMPLATE_SOURCE_FORMAT_PSD:
        from PIL import ImageDraw, ImageFont
        from psd_tools import PSDImage

        psd = PSDImage.open(io.BytesIO(data))
        composite = psd.composite().convert("RGB")
        draw = ImageDraw.Draw(composite)
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", int(mapping_fields.get("student_name", {}).get("font_size", 24)))
        except OSError:
            font = ImageFont.load_default()
        for key, value in field_values.items():
            placement = mapping_fields.get(key)
            if placement is None:
                continue
            x = int(composite.width * placement["x"])
            y = int(composite.height * placement["y"])
            draw.text((x, y), value, fill="black", font=font)
        out = io.BytesIO()
        composite.save(out, format="PNG")
        return out.getvalue()

    raise ValueError(_("Unsupported certificate source format."))
