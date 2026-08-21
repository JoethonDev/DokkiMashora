from django.utils.translation import gettext_lazy as _

ASSESSMENT_TYPE_QUIZ = "quiz"
ASSESSMENT_TYPE_SEMESTER_EXAM = "semester_exam"
ASSESSMENT_TYPE_FINAL_EXAM = "final_exam"
ASSESSMENT_TYPE_HOMEWORK = "homework"
ASSESSMENT_TYPE_ADDITIONAL = "additional_assessment"

# Active assessment vocabulary (P2-01): course scope -> Quiz only;
# diploma scope -> Homework / Semester Exam only. The deprecated constants
# above remain defined so historical rows keep their original meaning.
ASSESSMENT_TYPE_ACTIVE_CHOICES = [
    (ASSESSMENT_TYPE_QUIZ, _("Quiz")),
    (ASSESSMENT_TYPE_SEMESTER_EXAM, _("Semester exam")),
    (ASSESSMENT_TYPE_HOMEWORK, _("Homework")),
]

# Deprecated types remain in the model choices so existing rows display
# correctly; they are never offered as new/active options.
ASSESSMENT_TYPE_CHOICES = [
    (ASSESSMENT_TYPE_QUIZ, _("Quiz")),
    (ASSESSMENT_TYPE_SEMESTER_EXAM, _("Semester exam")),
    (ASSESSMENT_TYPE_FINAL_EXAM, _("Final exam")),
    (ASSESSMENT_TYPE_HOMEWORK, _("Homework")),
]

# Legacy / quarantine state for historical assessment rows (P2-01).
# Only ``active`` rows participate in new workflows. Deprecated or
# ambiguous rows are preserved read-only for inspection and remediation.
LEGACY_STATUS_ACTIVE = "active"
LEGACY_STATUS_ARCHIVED = "legacy_archived"
LEGACY_STATUS_REVIEW_REQUIRED = "legacy_review_required"

LEGACY_STATUS_CHOICES = [
    (LEGACY_STATUS_ACTIVE, _("Active")),
    (LEGACY_STATUS_ARCHIVED, _("Legacy archived")),
    (LEGACY_STATUS_REVIEW_REQUIRED, _("Legacy - review required")),
]

QUESTION_TYPE_MCQ = "mcq"
QUESTION_TYPE_WRITTEN = "written"

QUESTION_TYPE_CHOICES = [
    (QUESTION_TYPE_MCQ, _("Multiple choice")),
    (QUESTION_TYPE_WRITTEN, _("Written")),
]

GRADE_VISIBILITY_HIDDEN = "hidden"
GRADE_VISIBILITY_UNDER_REVIEW = "under_review"
GRADE_VISIBILITY_RELEASED = "released"

GRADE_VISIBILITY_CHOICES = [
    (GRADE_VISIBILITY_HIDDEN, _("Hidden")),
    (GRADE_VISIBILITY_UNDER_REVIEW, _("Under review")),
    (GRADE_VISIBILITY_RELEASED, _("Released")),
]

# Release mode (P2-04): locked defaults are Course Quiz = Automatic and
# Diploma Homework / Semester Exam = Manual; both are per-assessment
# configurable. Automatic release fires only after the final grade commits.
RELEASE_MODE_AUTOMATIC = "automatic"
RELEASE_MODE_MANUAL = "manual"

RELEASE_MODE_CHOICES = [
    (RELEASE_MODE_AUTOMATIC, _("Automatic")),
    (RELEASE_MODE_MANUAL, _("Manual")),
]

ATTEMPT_STATUS_ASSIGNED = "assigned"
ATTEMPT_STATUS_IN_PROGRESS = "in_progress"
ATTEMPT_STATUS_SUBMITTED = "submitted"
ATTEMPT_STATUS_UNDER_REVIEW = "under_review"
ATTEMPT_STATUS_GRADED = "graded"
ATTEMPT_STATUS_RELEASED = "released"
ATTEMPT_STATUS_EXPIRED = "expired"
ATTEMPT_STATUS_CANCELLED = "cancelled"

ATTEMPT_STATUS_CHOICES = [
    (ATTEMPT_STATUS_ASSIGNED, _("Assigned")),
    (ATTEMPT_STATUS_IN_PROGRESS, _("In progress")),
    (ATTEMPT_STATUS_SUBMITTED, _("Submitted")),
    (ATTEMPT_STATUS_UNDER_REVIEW, _("Under review")),
    (ATTEMPT_STATUS_GRADED, _("Graded")),
    (ATTEMPT_STATUS_RELEASED, _("Released")),
    (ATTEMPT_STATUS_EXPIRED, _("Expired")),
    (ATTEMPT_STATUS_CANCELLED, _("Cancelled")),
]

# Admin Semester Exam stage decision (P3-03): explicit Pass/Fail after all
# parts are graded, with actor/time/history, distinct from release.
EXAM_REVIEW_STATUS_NOT_REVIEWED = "not_reviewed"
EXAM_REVIEW_STATUS_PASSED = "passed"
EXAM_REVIEW_STATUS_FAILED = "failed"

EXAM_REVIEW_STATUS_CHOICES = [
    (EXAM_REVIEW_STATUS_NOT_REVIEWED, _("Not reviewed")),
    (EXAM_REVIEW_STATUS_PASSED, _("Passed")),
    (EXAM_REVIEW_STATUS_FAILED, _("Failed")),
]

HOMEWORK_STATUS_UPLOADED = "uploaded"
HOMEWORK_STATUS_SUBMITTED = "submitted"
HOMEWORK_STATUS_UNDER_REVIEW = "under_review"
HOMEWORK_STATUS_ACCEPTED = "accepted"
HOMEWORK_STATUS_NEEDS_REVISION = "needs_revision"
HOMEWORK_STATUS_REJECTED = "rejected"
HOMEWORK_STATUS_GRADED = "graded"
HOMEWORK_STATUS_RELEASED = "released"

HOMEWORK_STATUS_CHOICES = [
    (HOMEWORK_STATUS_UPLOADED, _("Uploaded")),
    (HOMEWORK_STATUS_SUBMITTED, _("Submitted")),
    (HOMEWORK_STATUS_UNDER_REVIEW, _("Under review")),
    (HOMEWORK_STATUS_ACCEPTED, _("Accepted")),
    (HOMEWORK_STATUS_NEEDS_REVISION, _("Needs revision")),
    (HOMEWORK_STATUS_REJECTED, _("Rejected")),
    (HOMEWORK_STATUS_GRADED, _("Graded")),
    (HOMEWORK_STATUS_RELEASED, _("Released")),
]

DELIVERY_MODE_ONLINE = "online"
DELIVERY_MODE_OFFLINE = "offline"

DELIVERY_MODE_CHOICES = (
    (DELIVERY_MODE_ONLINE, _("Online")),
    (DELIVERY_MODE_OFFLINE, _("Offline")),
)

SNAPSHOT_QUESTION_SOURCE_BANK = "bank"
SNAPSHOT_QUESTION_SOURCE_MANUAL = "manual"

SNAPSHOT_QUESTION_SOURCE_CHOICES = (
    (SNAPSHOT_QUESTION_SOURCE_BANK, _("Question bank")),
    (SNAPSHOT_QUESTION_SOURCE_MANUAL, _("Manual written")),
)

EXAM_OVERRIDE_ACTIVE = "active"
EXAM_OVERRIDE_USED = "used"
EXAM_OVERRIDE_CANCELLED = "cancelled"
EXAM_OVERRIDE_EXPIRED = "expired"

EXAM_OVERRIDE_STATUS_CHOICES = [
    (EXAM_OVERRIDE_ACTIVE, _("Active")),
    (EXAM_OVERRIDE_USED, _("Used")),
    (EXAM_OVERRIDE_CANCELLED, _("Cancelled")),
    (EXAM_OVERRIDE_EXPIRED, _("Expired")),
]

REVIEWER_ACCESS_ONLINE = "online"
REVIEWER_ACCESS_OFFLINE = "offline"
REVIEWER_ACCESS_CHOICES = [
    (REVIEWER_ACCESS_ONLINE, _("Request online submissions")),
    (REVIEWER_ACCESS_OFFLINE, _("Enter offline grades")),
]

REVIEWER_CLAIM_IN_PROGRESS = "in_progress"
REVIEWER_CLAIM_FINALIZED = "finalized"
REVIEWER_CLAIM_STATUS_CHOICES = [
    (REVIEWER_CLAIM_IN_PROGRESS, _("Pending finalization")),
    (REVIEWER_CLAIM_FINALIZED, _("Finalized")),
]

REVIEWER_SCOPE_COHORT = "cohort"
REVIEWER_SCOPE_SEMESTER = "semester"
REVIEWER_SCOPE_EXAM = "semester_exam"
REVIEWER_SCOPE_CHOICES = [
    (REVIEWER_SCOPE_COHORT, _("Cohort")),
    (REVIEWER_SCOPE_SEMESTER, _("Semester")),
    (REVIEWER_SCOPE_EXAM, _("Semester Exam")),
]
