from django import template

from apps.exams.constants import ASSIGNMENT_STATUS_IN_PROGRESS
from apps.exams.models import Assignment

register = template.Library()


@register.filter
def dict_key(d, key):
    return d.get(key)


@register.simple_tag
def get_pending_count(exam):
    """Return the count of in-progress assignments for an exam."""
    return Assignment.objects.filter(
        exam=exam, status=ASSIGNMENT_STATUS_IN_PROGRESS,
    ).count()
