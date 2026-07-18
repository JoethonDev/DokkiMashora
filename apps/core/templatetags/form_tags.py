"""Template filters for form field inspection and widget class injection."""

from django import template

register = template.Library()


@register.filter
def field_type(field):
    """Return the widget type name for a form field (select, checkbox, text, ...)."""
    widget = field.field.widget
    return widget.__class__.__name__.lower().replace("input", "").replace("widget", "")


@register.filter
def add_class(field, css_class):
    """Append a CSS class to a form field's widget without clobbering existing attrs."""
    widget = field.field.widget
    existing = widget.attrs.get("class", "")
    widget.attrs["class"] = (existing + " " + css_class).strip()
    described_by = []
    if field.help_text:
        described_by.append(f"{field.id_for_label}-help")
    if field.errors:
        widget.attrs["aria-invalid"] = "true"
        described_by.append(f"{field.id_for_label}-error")
    if field.form.non_field_errors():
        described_by.append(f"{field.form.prefix or 'form'}-non-field-errors")
    if described_by:
        widget.attrs["aria-describedby"] = " ".join(described_by)
    return field


@register.filter
def is_first_visible_field(field):
    return bool(field.form.visible_fields()) and field.form.visible_fields()[0].name == field.name


@register.filter
def get_item(dictionary, key):
    """Return dictionary[key] or empty string."""
    if isinstance(dictionary, dict):
        return dictionary.get(key, "")
    return ""
