from django.utils.translation import gettext_lazy as _

QUESTION_TYPE_MCQ = "mcq"
QUESTION_TYPE_WRITTEN = "written"
QUESTION_TYPE_CHOICES = [
    (QUESTION_TYPE_MCQ, _("Multiple choice")),
    (QUESTION_TYPE_WRITTEN, _("Written")),
]

ATTEMPT_STATUS_ASSIGNED = "assigned"
ATTEMPT_STATUS_IN_PROGRESS = "in_progress"
ATTEMPT_STATUS_COMPLETED = "completed"
ATTEMPT_STATUS_GRADED = "graded"
ATTEMPT_STATUS_CHOICES = [
    (ATTEMPT_STATUS_ASSIGNED, _("Assigned")),
    (ATTEMPT_STATUS_IN_PROGRESS, _("In Progress")),
    (ATTEMPT_STATUS_COMPLETED, _("Completed")),
    (ATTEMPT_STATUS_GRADED, _("Graded")),
]

RESULT_PASS = "pass"
RESULT_FAIL = "fail"
RESULT_CHOICES = [
    (RESULT_PASS, _("Pass")),
    (RESULT_FAIL, _("Fail")),
]
