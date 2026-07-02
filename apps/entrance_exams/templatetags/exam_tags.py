from django import template

from apps.entrance_exams.constants import ATTEMPT_STATUS_IN_PROGRESS
from apps.entrance_exams.models import EntranceAttempt

register = template.Library()


@register.simple_tag
def get_pending_count(exam):
    """Return the count of in-progress attempts for an exam."""
    return EntranceAttempt.objects.filter(
        exam=exam, status=ATTEMPT_STATUS_IN_PROGRESS,
    ).count()
