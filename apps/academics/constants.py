"""Academic structure: diplomas, cohorts, courses, versions, lectures, enrollments."""

from django.utils.translation import gettext_lazy as _

ENROLLMENT_STATUS_ACTIVE = "active"
ENROLLMENT_STATUS_EXPIRED = "expired"
ENROLLMENT_STATUS_RESTRICTED = "restricted"
ENROLLMENT_STATUS_PENDING = "pending"
ENROLLMENT_STATUS_COMPLETED = "completed"

ENROLLMENT_STATUS_CHOICES = (
    (ENROLLMENT_STATUS_PENDING, _("Pending")),
    (ENROLLMENT_STATUS_ACTIVE, _("Active")),
    (ENROLLMENT_STATUS_EXPIRED, _("Expired")),
    (ENROLLMENT_STATUS_RESTRICTED, _("Restricted")),
    (ENROLLMENT_STATUS_COMPLETED, _("Completed")),
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

SCOPE_WHOLE_DIPLOMA = "whole_diploma"
SCOPE_DIPLOMA_SEMESTER = "diploma_semester"
SCOPE_DIPLOMA_COHORT = "diploma_cohort"
SCOPE_SEMESTER_COHORT = "semester_cohort"
SCOPE_COURSE_VERSION = "course_version"

SCOPE_CHOICES = (
    (SCOPE_WHOLE_DIPLOMA, _("Whole diploma")),
    (SCOPE_DIPLOMA_SEMESTER, _("Diploma semester")),
    (SCOPE_DIPLOMA_COHORT, _("Diploma cohort")),
    (SCOPE_SEMESTER_COHORT, _("Semester within cohort")),
    (SCOPE_COURSE_VERSION, _("Course version")),
)

WATCH_STATUS_IN_PROGRESS = "in_progress"
WATCH_STATUS_COMPLETED = "completed"
WATCH_STATUS_VIOLATED = "violated"

WATCH_STATUS_CHOICES = (
    (WATCH_STATUS_IN_PROGRESS, _("In progress")),
    (WATCH_STATUS_COMPLETED, _("Completed")),
    (WATCH_STATUS_VIOLATED, _("Violated")),
)

VIOLATION_NONE = "none"
VIOLATION_SPEED = "speed"
VIOLATION_FORWARD_SKIP = "forward_skip"
VIOLATION_INVALID_SEQUENCE = "invalid_sequence"
VIOLATION_INVALID_POSITION = "invalid_position"

VIOLATION_CHOICES = (
    (VIOLATION_NONE, _("None")),
    (VIOLATION_SPEED, _("Speed")),
    (VIOLATION_FORWARD_SKIP, _("Forward skip")),
    (VIOLATION_INVALID_SEQUENCE, _("Invalid sequence")),
    (VIOLATION_INVALID_POSITION, _("Invalid position")),
)
