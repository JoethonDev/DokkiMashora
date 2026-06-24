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

SCOPE_STANDALONE_COURSE = "standalone_course"
SCOPE_WHOLE_DIPLOMA = "whole_diploma"
SCOPE_DIPLOMA_SEMESTER = "diploma_semester"
SCOPE_DIPLOMA_COHORT = "diploma_cohort"
SCOPE_SEMESTER_COHORT = "semester_cohort"

SCOPE_CHOICES = (
    (SCOPE_STANDALONE_COURSE, _("Standalone course")),
    (SCOPE_WHOLE_DIPLOMA, _("Whole diploma")),
    (SCOPE_DIPLOMA_SEMESTER, _("Diploma semester")),
    (SCOPE_DIPLOMA_COHORT, _("Diploma cohort")),
    (SCOPE_SEMESTER_COHORT, _("Semester within cohort")),
)
