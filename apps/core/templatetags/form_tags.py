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
    return field
