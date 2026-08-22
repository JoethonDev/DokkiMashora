"""Template filters for form field inspection and widget class injection."""

from django import template

from apps.core.forms import CANONICAL_WIDGET_CLASSES, DEFAULT_WIDGET_CLASS, WIDGET_CLASSES

register = template.Library()


@register.filter
def field_type(field):
    """Return the widget type name for a form field (select, checkbox, text, ...)."""
    widget = field.field.widget
    name = widget.__class__.__name__.lower().replace("input", "").replace("widget", "")
    if name == "checkbox":
        return "checkbox"
    if name == "radioselect":
        return "radio"
    if name == "checkboxselectmultiple":
        return "checkbox"
    return name


@register.filter
def widget_class(field):
    """Render the widget with the canonical class (R1 safety net).

    Non-mutating (uses as_widget), idempotent, imports the single mapping
    from apps/core/forms. Also wires aria-describedby/aria-invalid.
    """
    widget = field.field.widget
    canonical = WIDGET_CLASSES.get(type(widget).__name__, DEFAULT_WIDGET_CLASS)
    existing = set(widget.attrs.get("class", "").split()) - CANONICAL_WIDGET_CLASSES
    attrs = {"class": " ".join(sorted(existing | {canonical}))}
    described_by = []
    if field.help_text:
        described_by.append(f"{field.id_for_label}-help")
    if field.errors:
        attrs["aria-invalid"] = "true"
        described_by.append(f"{field.id_for_label}-error")
    if described_by:
        attrs["aria-describedby"] = " ".join(described_by)
    return field.as_widget(attrs=attrs)


@register.filter
def get_item(dictionary, key):
    """Return dictionary[key] or empty string."""
    if isinstance(dictionary, dict):
        return dictionary.get(key, "")
    return ""
