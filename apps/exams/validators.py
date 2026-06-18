from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


def validate_exam_open(open_at, close_at):
    if open_at >= close_at:
        raise ValidationError(_("Open time must be before close time."))


def validate_exam_timeframe(open_at, close_at):
    now = timezone.now()
    if close_at <= now:
        raise ValidationError(_("Close time must be in the future."))
