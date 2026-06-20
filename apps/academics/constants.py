"""Academic structure: diplomas, cohorts, courses, versions, lectures, enrollments."""

from django.utils.translation import gettext_lazy as _

ENROLLMENT_STATUS_ACTIVE = "active"
ENROLLMENT_STATUS_EXPIRED = "expired"
ENROLLMENT_STATUS_RESTRICTED = "restricted"
ENROLLMENT_STATUS_PENDING = "pending"

ENROLLMENT_STATUS_CHOICES = (
    (ENROLLMENT_STATUS_PENDING, _("Pending")),
    (ENROLLMENT_STATUS_ACTIVE, _("Active")),
    (ENROLLMENT_STATUS_EXPIRED, _("Expired")),
    (ENROLLMENT_STATUS_RESTRICTED, _("Restricted")),
)

AUDIO_PROCESSING_PENDING = "pending"
AUDIO_PROCESSING_PROCESSING = "processing"
AUDIO_PROCESSING_COMPLETED = "completed"
AUDIO_PROCESSING_FAILED = "failed"

AUDIO_PROCESSING_STATUS_CHOICES = (
    (AUDIO_PROCESSING_PENDING, _("Pending")),
    (AUDIO_PROCESSING_PROCESSING, _("Processing")),
    (AUDIO_PROCESSING_COMPLETED, _("Completed")),
    (AUDIO_PROCESSING_FAILED, _("Failed")),
)
