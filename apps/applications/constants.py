"""Constants for the applications domain."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

PROGRAM_APP_STATUS_SUBMITTED = "submitted"
PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED = "account_activated"
PROGRAM_APP_STATUS_PAYMENT_PENDING = "payment_pending"
PROGRAM_APP_STATUS_INVITED = "invited_to_entrance"
PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED = "entrance_submitted"
PROGRAM_APP_STATUS_INTERVIEW_REQUESTED = "interview_requested"
PROGRAM_APP_STATUS_ACCEPTED = "accepted"
PROGRAM_APP_STATUS_REJECTED = "rejected"
PROGRAM_APP_STATUS_DOCUMENTS_REQUESTED = "documents_requested"
PROGRAM_APP_STATUS_DOCUMENTS_SUBMITTED = "documents_submitted"
PROGRAM_APP_STATUS_SURVEY_REQUESTED = "survey_requested"
PROGRAM_APP_STATUS_SURVEY_SUBMITTED = "survey_submitted"
PROGRAM_APP_STATUS_READY_FOR_ACTIVATION = "ready_for_activation"
PROGRAM_APP_STATUS_UNDER_REVIEW = "under_review"  # legacy — not used by new workflow code
PROGRAM_APP_STATUS_ACTIVATED = "activated"

PROGRAM_APP_STATUS_CHOICES = (
    (PROGRAM_APP_STATUS_SUBMITTED, _("Submitted")),
    (PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED, _("Account Activated")),
    (PROGRAM_APP_STATUS_PAYMENT_PENDING, _("Payment Pending")),
    (PROGRAM_APP_STATUS_INVITED, _("Invited to Entrance Assessment")),
    (PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED, _("Entrance Submitted")),
    (PROGRAM_APP_STATUS_INTERVIEW_REQUESTED, _("Interview Requested")),
    (PROGRAM_APP_STATUS_ACCEPTED, _("Accepted")),
    (PROGRAM_APP_STATUS_REJECTED, _("Rejected")),
    (PROGRAM_APP_STATUS_DOCUMENTS_REQUESTED, _("Documents Requested")),
    (PROGRAM_APP_STATUS_DOCUMENTS_SUBMITTED, _("Documents Submitted")),
    (PROGRAM_APP_STATUS_SURVEY_REQUESTED, _("Survey Requested")),
    (PROGRAM_APP_STATUS_SURVEY_SUBMITTED, _("Survey Submitted")),
    (PROGRAM_APP_STATUS_READY_FOR_ACTIVATION, _("Ready for Activation")),
    (PROGRAM_APP_STATUS_ACTIVATED, _("Activated")),
    (PROGRAM_APP_STATUS_UNDER_REVIEW, _("Under review (legacy)")),
)

# under_review is kept for existing data but not used by new workflow code

# All statuses that block duplicate application for the same target
ACTIVE_PROGRAM_APP_STATUSES = (
    PROGRAM_APP_STATUS_SUBMITTED,
    PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED,
    PROGRAM_APP_STATUS_PAYMENT_PENDING,
    PROGRAM_APP_STATUS_INVITED,
    PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED,
    PROGRAM_APP_STATUS_INTERVIEW_REQUESTED,
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_DOCUMENTS_REQUESTED,
    PROGRAM_APP_STATUS_DOCUMENTS_SUBMITTED,
    PROGRAM_APP_STATUS_SURVEY_REQUESTED,
    PROGRAM_APP_STATUS_SURVEY_SUBMITTED,
    PROGRAM_APP_STATUS_READY_FOR_ACTIVATION,
    PROGRAM_APP_STATUS_ACTIVATED,
)

TERMINAL_PROGRAM_APP_STATUSES = (PROGRAM_APP_STATUS_REJECTED,)

PROGRAM_TYPE_DIPLOMA = "diploma"
PROGRAM_TYPE_COURSE = "course"

PROGRAM_TYPE_CHOICES = (
    (PROGRAM_TYPE_DIPLOMA, _("Diploma (Cohort)")),
    (PROGRAM_TYPE_COURSE, _("Course")),
)

# Diploma delivery is selected on the application and copied to each
# Semester Exam code when the exam roster is generated.
DELIVERY_MODE_ONLINE = "online"
DELIVERY_MODE_OFFLINE = "offline"
DELIVERY_MODE_CHOICES = (
    (DELIVERY_MODE_ONLINE, _("Online")),
    (DELIVERY_MODE_OFFLINE, _("Offline")),
)

# Decision action constants
DECISION_ACTION_ACCOUNT_ACTIVATED = "account_activated"
DECISION_ACTION_PAYMENT_PENDING = "payment_pending"
DECISION_ACTION_INVITED_TO_ENTRANCE = "invited_to_entrance"
DECISION_ACTION_ENTRANCE_SUBMITTED = "entrance_submitted"
DECISION_ACTION_REQUEST_INTERVIEW = "request_interview"
DECISION_ACTION_ACCEPT = "accept"
DECISION_ACTION_REJECT = "reject"
DECISION_ACTION_REVERT = "revert"
DECISION_ACTION_NOTE = "note"
DECISION_ACTION_REQUEST_DOCUMENTS = "request_documents"
DECISION_ACTION_SUBMIT_DOCUMENTS = "submit_documents"
DECISION_ACTION_REQUEST_SURVEY = "request_survey"
DECISION_ACTION_SUBMIT_SURVEY = "submit_survey"
DECISION_ACTION_ACTIVATE = "activate"

DECISION_ACTION_CHOICES = (
    (DECISION_ACTION_ACCOUNT_ACTIVATED, _("Account Activated")),
    (DECISION_ACTION_PAYMENT_PENDING, _("Payment Pending")),
    (DECISION_ACTION_INVITED_TO_ENTRANCE, _("Invited to Entrance")),
    (DECISION_ACTION_ENTRANCE_SUBMITTED, _("Entrance Submitted")),
    (DECISION_ACTION_REQUEST_INTERVIEW, _("Request Interview")),
    (DECISION_ACTION_ACCEPT, _("Accepted")),
    (DECISION_ACTION_REJECT, _("Rejected")),
    (DECISION_ACTION_REVERT, _("Reverted")),
    (DECISION_ACTION_NOTE, _("Note")),
    (DECISION_ACTION_REQUEST_DOCUMENTS, _("Documents Requested")),
    (DECISION_ACTION_SUBMIT_DOCUMENTS, _("Documents Submitted")),
    (DECISION_ACTION_REQUEST_SURVEY, _("Survey Requested")),
    (DECISION_ACTION_SUBMIT_SURVEY, _("Survey Submitted")),
    (DECISION_ACTION_ACTIVATE, _("Activated")),
)

REVERTABLE_ACTIONS = frozenset({DECISION_ACTION_ACCEPT, DECISION_ACTION_REJECT})

# Document request statuses
DOCUMENT_REQUEST_STATUS_REQUESTED = "requested"
DOCUMENT_REQUEST_STATUS_SUBMITTED = "submitted"
DOCUMENT_REQUEST_STATUS_EXPIRED = "expired"
DOCUMENT_REQUEST_STATUS_CANCELLED = "cancelled"

DOCUMENT_REQUEST_STATUS_CHOICES = (
    (DOCUMENT_REQUEST_STATUS_REQUESTED, _("Requested")),
    (DOCUMENT_REQUEST_STATUS_SUBMITTED, _("Submitted")),
    (DOCUMENT_REQUEST_STATUS_EXPIRED, _("Expired")),
    (DOCUMENT_REQUEST_STATUS_CANCELLED, _("Cancelled")),
)

# Email action constants
EMAIL_ACTION_ENTRANCE_EXAM_INVITATION = "entrance_exam_invitation"
EMAIL_ACTION_REJECTION = "rejection"
EMAIL_ACTION_ACCEPTANCE = "acceptance"
EMAIL_ACTION_COURSE_PAYMENT_INSTRUCTIONS = "course_payment_instructions"
EMAIL_ACTION_DIPLOMA_ENTRANCE_EXAM_SCHEDULE = "diploma_entrance_exam_schedule"
EMAIL_ACTION_DIPLOMA_INTERVIEW_REQUEST = "diploma_interview_request"
EMAIL_ACTION_DIPLOMA_DOCUMENT_UPLOAD_REQUEST = "diploma_document_upload_request"
EMAIL_ACTION_NOTE = "note"
EMAIL_ACTION_EXAM_RESULT = "entrance_exam_result"
EMAIL_ACTION_APPLICATION_PAYMENT_PENDING = "application_payment_pending"
EMAIL_ACTION_APPLICATION_SURVEY_REQUEST = "application_survey_request"

EMAIL_ACTION_CHOICES = (
    (EMAIL_ACTION_ENTRANCE_EXAM_INVITATION, _("Entrance exam invitation")),
    (EMAIL_ACTION_REJECTION, _("Rejection")),
    (EMAIL_ACTION_ACCEPTANCE, _("Acceptance")),
    (EMAIL_ACTION_COURSE_PAYMENT_INSTRUCTIONS, _("Course payment instructions")),
    (EMAIL_ACTION_DIPLOMA_ENTRANCE_EXAM_SCHEDULE, _("Diploma entrance exam schedule")),
    (EMAIL_ACTION_DIPLOMA_INTERVIEW_REQUEST, _("Diploma interview request")),
    (EMAIL_ACTION_DIPLOMA_DOCUMENT_UPLOAD_REQUEST, _("Diploma document upload request")),
    (EMAIL_ACTION_APPLICATION_PAYMENT_PENDING, _("Application payment pending")),
    (EMAIL_ACTION_APPLICATION_SURVEY_REQUEST, _("Application survey request")),
    (EMAIL_ACTION_NOTE, _("Note")),
    (EMAIL_ACTION_EXAM_RESULT, _("Entrance exam result")),
)
