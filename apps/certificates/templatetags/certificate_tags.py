from django import template

try:
    import arabic_reshaper
    from arabic_reshaper import ArabicReshaper
    from bidi.algorithm import get_display
except ImportError:  # pragma: no cover - xhtml2pdf installs both
    arabic_reshaper = None
    ArabicReshaper = None
    get_display = None

register = template.Library()

if ArabicReshaper is not None:
    # Cairo exposes base Arabic glyphs but not every isolated presentation form.
    # Keeping isolated letters unshaped avoids missing-glyph boxes in the PDF.
    _reshaper = ArabicReshaper({"use_unshaped_instead_of_isolated": True})
else:  # pragma: no cover
    _reshaper = None


@register.simple_tag(takes_context=True)
def pdf_text(context, value):
    """Shape Arabic text for xhtml2pdf only; browser preview stays plain text.

    xhtml2pdf has no bidi engine of its own. The certificate template is rendered
    with ``for_pdf=True`` for the PDF path and ``False`` for the designer iframe.
    """
    if value is None:
        return ""
    if not context.get("for_pdf") or _reshaper is None or not value:
        return value
    return get_display(_reshaper.reshape(str(value)))
