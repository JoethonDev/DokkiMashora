from django.utils.translation import gettext_lazy as _

QUESTION_TYPE_MCQ = "mcq"
QUESTION_TYPE_ESSAY = "essay"
QUESTION_TYPE_CHOICES = [
    (QUESTION_TYPE_MCQ, _("Multiple choice")),
    (QUESTION_TYPE_ESSAY, _("Essay")),
]

ASSIGNMENT_STATUS_ASSIGNED = "assigned"
ASSIGNMENT_STATUS_IN_PROGRESS = "in_progress"
ASSIGNMENT_STATUS_COMPLETED = "completed"
ASSIGNMENT_STATUS_GRADED = "graded"
ASSIGNMENT_STATUS_CHOICES = [
    (ASSIGNMENT_STATUS_ASSIGNED, _("Assigned")),
    (ASSIGNMENT_STATUS_IN_PROGRESS, _("In progress")),
    (ASSIGNMENT_STATUS_COMPLETED, _("Completed")),
    (ASSIGNMENT_STATUS_GRADED, _("Graded")),
]
