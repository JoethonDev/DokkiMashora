from __future__ import annotations

from django.utils.translation import gettext_lazy as _

# --- Business sections -------------------------------------------------------
# Audit rows are grouped by the business area an administrator thinks in, not
# by Django app. The same sections drive the audit filters, the action
# optgroups, and the dashboard-style section counts.
SECTION_ADMISSIONS = "admissions"
SECTION_ACADEMICS = "academics"
SECTION_ASSESSMENTS = "assessments"
SECTION_ATTENDANCE = "attendance"
SECTION_PROGRESSION = "progression"
SECTION_PAYMENTS = "payments"
SECTION_CERTIFICATES = "certificates"
SECTION_COMMUNICATIONS = "communications"
SECTION_ACCOUNTS = "accounts"
SECTION_SETTINGS = "settings"
SECTION_SYSTEM = "system"
SECTION_SECURITY = "security"

AUDIT_SECTION_CHOICES = (
    (SECTION_ADMISSIONS, _("Admissions")),
    (SECTION_ACADEMICS, _("Academics")),
    (SECTION_ASSESSMENTS, _("Assessments")),
    (SECTION_ATTENDANCE, _("Attendance")),
    (SECTION_PROGRESSION, _("Progression")),
    (SECTION_PAYMENTS, _("Payments")),
    (SECTION_CERTIFICATES, _("Certificates")),
    (SECTION_COMMUNICATIONS, _("Communications")),
    (SECTION_ACCOUNTS, _("Accounts & access")),
    (SECTION_SETTINGS, _("Settings")),
    (SECTION_SYSTEM, _("System")),
    (SECTION_SECURITY, _("Security")),
)

AUDIT_SECTION_KEYS = tuple(key for key, _label in AUDIT_SECTION_CHOICES)

# Prefix-based fallback for historic rows and for callers that do not pass a
# section explicitly. Exact action constants are resolved first.
ACTION_SECTION_PREFIXES = {
    "application.": SECTION_ADMISSIONS,
    "admission.": SECTION_ADMISSIONS,
    "entrance_exam.": SECTION_ADMISSIONS,
    "intake_": SECTION_ADMISSIONS,
    "exam.": SECTION_ASSESSMENTS,
    "question.": SECTION_ASSESSMENTS,
    "grade.": SECTION_ASSESSMENTS,
    "student.": SECTION_ASSESSMENTS,
    "exam_access_override.": SECTION_ASSESSMENTS,
    "enrollment.": SECTION_ACADEMICS,
    "academic.": SECTION_ACADEMICS,
    "promotion.": SECTION_PROGRESSION,
    "progression.": SECTION_PROGRESSION,
    "certificate.": SECTION_CERTIFICATES,
    "bulk_email.": SECTION_COMMUNICATIONS,
    "communication.": SECTION_COMMUNICATIONS,
    "payment.": SECTION_PAYMENTS,
    "user_import.": SECTION_ACCOUNTS,
    "staff_assignment.": SECTION_ACCOUNTS,
    "cluster.": SECTION_ACCOUNTS,
    "user.": SECTION_ACCOUNTS,
    "attendance.": SECTION_ATTENDANCE,
    "settings.": SECTION_SETTINGS,
    "system.": SECTION_SYSTEM,
    "security.": SECTION_SECURITY,
    "admin.": SECTION_SYSTEM,
    "request.": SECTION_SYSTEM,
    "demo.": SECTION_SYSTEM,
}

# --- Action codes ------------------------------------------------------------
ACTION_APPLICATION_STATUS_CHANGE = "application.status_change"
ACTION_APPLICATION_APPROVED = "application.approved"
ACTION_APPLICATION_REJECTED = "application.rejected"
ACTION_APPLICATION_ACTIVATED = "application.activated"
ACTION_APPLICATION_ACCOUNT_ACTIVATED = "application.account_activated"
ACTION_APPLICATION_ENTRANCE_SUBMITTED = "application.entrance_submitted"
ACTION_APPLICATION_INTERVIEW_REQUESTED = "application.interview_requested"
ACTION_APPLICATION_PAID = "application.paid"
ACTION_APPLICATION_DECISION_REVERTED = "application.decision_reverted"
ACTION_STUDENT_ACCESS_CHANGE = "student.access_change"
ACTION_ENROLLMENT_CHANGE = "enrollment.change"
ACTION_ENROLLMENT_DELETED = "enrollment.deleted"
ACTION_ACADEMIC_DELETED = "academic.deleted"
ACTION_ACADEMIC_CREATED = "academic.created"
ACTION_ACADEMIC_UPDATED = "academic.updated"
ACTION_ACADEMIC_TOGGLED = "academic.toggled"
ACTION_EXAM_CREATED = "exam.created"
ACTION_EXAM_UPDATED = "exam.updated"
ACTION_EXAM_TOGGLED = "exam.toggled"
ACTION_EXAM_DELETED = "exam.deleted"
ACTION_ENTRANCE_EXAM_COMPLETED = "entrance_exam.completed"
ACTION_ENTRANCE_EXAM_CREATED = "entrance_exam.created"
ACTION_ENTRANCE_EXAM_UPDATED = "entrance_exam.updated"
ACTION_ENTRANCE_EXAM_TOGGLED = "entrance_exam.toggled"
ACTION_ENTRANCE_QUESTION_CREATED = "entrance_exam.question_created"
ACTION_ENTRANCE_QUESTION_UPDATED = "entrance_exam.question_updated"
ACTION_ENTRANCE_QUESTION_DELETED = "entrance_exam.question_deleted"
ACTION_ENTRANCE_EXAM_GRADED = "entrance_exam.graded"
ACTION_ADMISSIONS_EMAIL_SENT = "admission.email_sent"
ACTION_PROGRESSION_GRADE_SYNCED = "progression.grade_synced"
ACTION_REVIEWER_ASSIGNMENT_REVOKED = "exam.reviewer_assignment_revoked"
ACTION_QUESTION_CREATED = "question.created"
ACTION_QUESTION_UPDATED = "question.updated"
ACTION_QUESTION_DELETED = "question.deleted"
ACTION_GRADE_CHANGED = "grade.changed"
ACTION_GRADE_OVERRIDE = "grade.override"
ACTION_GRADE_RELEASED = "grade.released"
ACTION_GRADE_HIDDEN = "grade.hidden"
ACTION_PROMOTION = "promotion.promoted"
ACTION_DEMOTION = "promotion.demoted"
ACTION_COMPLETED = "progression.completed"
ACTION_GRADUATED = "progression.graduated"
ACTION_LOCK = "promotion.locked"
ACTION_UNLOCK = "promotion.unlocked"
ACTION_RETAKE = "promotion.retake"
ACTION_CERTIFICATE_GENERATED = "certificate.generated"
ACTION_CERTIFICATE_REVOKED = "certificate.revoked"
ACTION_CERTIFICATE_REGENERATED = "certificate.regenerated"
ACTION_CERTIFICATE_TEMPLATE_DELETED = "certificate.template_deleted"
ACTION_BULK_EMAIL_SENT = "bulk_email.sent"
ACTION_PAYMENT_UPLOAD_PREVIEWED = "payment.upload_previewed"
ACTION_PAYMENT_UPLOAD_CONFIRMED = "payment.upload_confirmed"
ACTION_PAYMENT_RECORD_UPDATED = "payment.record_updated"
ACTION_PAYMENT_RECORD_DELETED = "payment.record_deleted"
ACTION_PAYMENT_WAIVER_GRANTED = "payment.waiver_granted"
ACTION_PAYMENT_WAIVER_REVOKED = "payment.waiver_revoked"
ACTION_USER_IMPORT_PREVIEWED = "user_import.previewed"
ACTION_USER_IMPORT_CONFIRMED = "user_import.confirmed"
ACTION_EXAM_ACCESS_OVERRIDE_CREATED = "exam_access_override.created"
ACTION_EXAM_ACCESS_OVERRIDE_CANCELLED = "exam_access_override.cancelled"
ACTION_EXAM_ACCESS_OVERRIDE_USED = "exam_access_override.used"
ACTION_RULE_REEVALUATION = "progression.rule_reevaluation"
ACTION_SEMESTER_FINALIZED = "progression.semester_finalized"
ACTION_SEMESTER_REEVALUATED = "progression.semester_reevaluated"
ACTION_STAFF_ASSIGNMENT_CREATED = "staff_assignment.created"
ACTION_STAFF_ASSIGNMENT_REVOKED = "staff_assignment.revoked"
ACTION_CLUSTER_CREATED = "cluster.created"
ACTION_CLUSTER_MEMBERSHIP_ADDED = "cluster.membership_added"
ACTION_CLUSTER_MEMBERSHIP_REMOVED = "cluster.membership_removed"
ACTION_INTAKE_FORM_CREATED = "intake_form.created"
ACTION_INTAKE_FORM_TOGGLED = "intake_form.toggled"
ACTION_INTAKE_FORM_FIELD_CREATED = "intake_form.field_created"
ACTION_INTAKE_FORM_FIELD_DELETED = "intake_form.field_deleted"
ACTION_INTAKE_REQUEST_SENT = "intake_request.sent"
ACTION_USER_CREATED = "user.created"
ACTION_USER_UPDATED = "user.updated"
ACTION_USER_DELETED = "user.deleted"
ACTION_ATTENDANCE_SESSION_DELETED = "attendance.session_deleted"
ACTION_ATTENDANCE_PERIOD_WINDOW_UPDATED = "attendance.period_window_updated"
ACTION_ATTENDANCE_SESSION_WINDOW_UPDATED = "attendance.session_window_updated"
ACTION_ATTENDANCE_SESSION_CREATED = "attendance.session_created"
ACTION_ATTENDANCE_SESSION_OPENED = "attendance.session_opened"
ACTION_ATTENDANCE_SESSION_CLOSED = "attendance.session_closed"
ACTION_ATTENDANCE_PERIOD_DAY_CREATED = "attendance.period_day_created"

ACTION_ASSESSMENT_CODES_GENERATED = "exam.codes_generated"
ACTION_ASSESSMENT_RECALCULATED = "exam.recalculated"
ACTION_REVIEWER_ASSIGNMENT_CREATED = "exam.reviewer_assignment_created"

ACTION_PROGRESSION_CONFIG_UPDATED = "progression.config_updated"
ACTION_PROGRESSION_INTERVIEW_REQUESTED = "progression.interview_requested"
ACTION_PROGRESSION_INTERVIEW_COMPLETED = "progression.interview_completed"
ACTION_PROGRESSION_REJOIN_CREATED = "progression.rejoin_created"
ACTION_PROGRESSION_REJOIN_ACTIVATED = "progression.rejoin_activated"
ACTION_PROGRESSION_REJOIN_CANCELLED = "progression.rejoin_cancelled"

ACTION_CERTIFICATE_TEMPLATE_CREATED = "certificate.template_created"
ACTION_CERTIFICATE_TEMPLATE_UPDATED = "certificate.template_updated"

ACTION_COMMUNICATION_TEMPLATE_CREATED = "communication.template_created"
ACTION_COMMUNICATION_TEMPLATE_UPDATED = "communication.template_updated"
ACTION_COMMUNICATION_FOOTER_UPDATED = "communication.footer_updated"
ACTION_BULK_EMAIL_DISCARDED = "bulk_email.discarded"

# Settings, operations, and system tracing.
ACTION_SETTINGS_UPDATED = "settings.updated"
ACTION_EMAIL_CONFIG_CREATED = "settings.email_config.created"
ACTION_EMAIL_CONFIG_UPDATED = "settings.email_config.updated"
ACTION_EMAIL_CONFIG_ACTIVATED = "settings.email_config.activated"
ACTION_EMAIL_CONFIG_DELETED = "settings.email_config.deleted"
ACTION_EMAIL_CONFIG_TESTED = "settings.email_config.tested"
ACTION_LANDING_UPDATED = "settings.landing_updated"
ACTION_ANNOUNCEMENT_UPDATED = "settings.announcement_updated"
ACTION_REDIS_KEY_CREATED = "settings.redis_key.created"
ACTION_REDIS_KEY_DELETED = "settings.redis_key.deleted"
ACTION_SYSTEM_EMAIL_QUEUED = "system.email.queued"
ACTION_SYSTEM_EMAIL_SENT = "system.email.sent"
ACTION_SYSTEM_EMAIL_FAILED = "system.email.failed"
ACTION_SYSTEM_EMAIL_SKIPPED = "system.email.skipped"
ACTION_SYSTEM_TASK_STARTED = "system.task.started"
ACTION_SYSTEM_TASK_COMPLETED = "system.task.completed"
ACTION_SYSTEM_TASK_FAILED = "system.task.failed"
ACTION_SYSTEM_TASK_RETRIED = "system.task.retried"
ACTION_ADMIN_CHANGE = "admin.change"
ACTION_REQUEST_MUTATION = "request.mutation"
ACTION_SECURITY_LOGIN = "security.login"
ACTION_SECURITY_LOGOUT = "security.logout"
ACTION_SECURITY_LOGIN_FAILED = "security.login_failed"
ACTION_DEMO_DATA_SEEDED = "demo.data_seeded"

AUDIT_ACTION_CHOICES = (
    # Admissions
    (ACTION_APPLICATION_STATUS_CHANGE, _("Application status change")),
    (ACTION_APPLICATION_APPROVED, _("Application approved")),
    (ACTION_APPLICATION_REJECTED, _("Application rejected")),
    (ACTION_APPLICATION_ACTIVATED, _("Application activated")),
    (ACTION_APPLICATION_ACCOUNT_ACTIVATED, _("Application account activated")),
    (ACTION_APPLICATION_ENTRANCE_SUBMITTED, _("Entrance exam submitted")),
    (ACTION_APPLICATION_INTERVIEW_REQUESTED, _("Interview requested")),
    (ACTION_APPLICATION_PAID, _("Application payment recorded")),
    (ACTION_APPLICATION_DECISION_REVERTED, _("Application decision reverted")),
    (ACTION_ENTRANCE_EXAM_COMPLETED, _("Entrance exam completed")),
    (ACTION_ENTRANCE_EXAM_CREATED, _("Entrance exam created")),
    (ACTION_ENTRANCE_EXAM_UPDATED, _("Entrance exam updated")),
    (ACTION_ENTRANCE_EXAM_TOGGLED, _("Entrance exam visibility changed")),
    (ACTION_ENTRANCE_QUESTION_CREATED, _("Entrance exam question created")),
    (ACTION_ENTRANCE_QUESTION_UPDATED, _("Entrance exam question updated")),
    (ACTION_ENTRANCE_QUESTION_DELETED, _("Entrance exam question deleted")),
    (ACTION_ENTRANCE_EXAM_GRADED, _("Entrance exam graded")),
    (ACTION_ADMISSIONS_EMAIL_SENT, _("Admissions email sent")),
    (ACTION_INTAKE_FORM_CREATED, _("Intake form created")),
    (ACTION_INTAKE_FORM_TOGGLED, _("Intake form status changed")),
    (ACTION_INTAKE_FORM_FIELD_CREATED, _("Intake form field created")),
    (ACTION_INTAKE_FORM_FIELD_DELETED, _("Intake form field deleted")),
    (ACTION_INTAKE_REQUEST_SENT, _("Intake form requested")),
    # Academics
    (ACTION_ACADEMIC_DELETED, _("Academic record deleted")),
    (ACTION_ACADEMIC_CREATED, _("Academic record created")),
    (ACTION_ACADEMIC_UPDATED, _("Academic record updated")),
    (ACTION_ACADEMIC_TOGGLED, _("Academic record visibility changed")),
    (ACTION_ENROLLMENT_CHANGE, _("Enrollment change")),
    (ACTION_ENROLLMENT_DELETED, _("Enrollment deleted")),
    # Assessments
    (ACTION_STUDENT_ACCESS_CHANGE, _("Student exam access change")),
    (ACTION_EXAM_CREATED, _("Assessment created")),
    (ACTION_EXAM_UPDATED, _("Assessment updated")),
    (ACTION_EXAM_TOGGLED, _("Assessment toggled")),
    (ACTION_EXAM_DELETED, _("Assessment deleted")),
    (ACTION_QUESTION_CREATED, _("Question created")),
    (ACTION_QUESTION_UPDATED, _("Question updated")),
    (ACTION_QUESTION_DELETED, _("Question deleted")),
    (ACTION_GRADE_CHANGED, _("Grade changed")),
    (ACTION_GRADE_OVERRIDE, _("Grade override")),
    (ACTION_GRADE_RELEASED, _("Grade released")),
    (ACTION_GRADE_HIDDEN, _("Grade hidden")),
    (ACTION_EXAM_ACCESS_OVERRIDE_CREATED, _("Exam access override created")),
    (ACTION_EXAM_ACCESS_OVERRIDE_CANCELLED, _("Exam access override cancelled")),
    (ACTION_EXAM_ACCESS_OVERRIDE_USED, _("Exam access override used")),
    (ACTION_ASSESSMENT_CODES_GENERATED, _("Exam codes generated")),
    (ACTION_ASSESSMENT_RECALCULATED, _("Assessment totals recalculated")),
    (ACTION_REVIEWER_ASSIGNMENT_CREATED, _("Reviewer assignment created")),
    (ACTION_REVIEWER_ASSIGNMENT_REVOKED, _("Reviewer assignment revoked")),
    # Attendance
    (ACTION_ATTENDANCE_SESSION_DELETED, _("Attendance session deleted")),
    (ACTION_ATTENDANCE_PERIOD_WINDOW_UPDATED, _("Attendance period window updated")),
    (ACTION_ATTENDANCE_SESSION_WINDOW_UPDATED, _("Attendance session window updated")),
    (ACTION_ATTENDANCE_SESSION_CREATED, _("Attendance session created")),
    (ACTION_ATTENDANCE_SESSION_OPENED, _("Attendance session opened")),
    (ACTION_ATTENDANCE_SESSION_CLOSED, _("Attendance session closed")),
    (ACTION_ATTENDANCE_PERIOD_DAY_CREATED, _("Attendance period day created")),
    # Progression
    (ACTION_PROMOTION, _("Promotion")),
    (ACTION_DEMOTION, _("Demotion")),
    (ACTION_COMPLETED, _("Enrollment completed")),
    (ACTION_GRADUATED, _("Student graduated")),
    (ACTION_LOCK, _("Lock")),
    (ACTION_UNLOCK, _("Unlock")),
    (ACTION_RETAKE, _("Retake")),
    (ACTION_RULE_REEVALUATION, _("Progression rule re-evaluation")),
    (ACTION_SEMESTER_FINALIZED, _("Semester finalized")),
    (ACTION_SEMESTER_REEVALUATED, _("Semester re-evaluated")),
    (ACTION_PROGRESSION_GRADE_SYNCED, _("Progression grades synced")),
    (ACTION_PROGRESSION_CONFIG_UPDATED, _("Progression settings updated")),
    (ACTION_PROGRESSION_INTERVIEW_REQUESTED, _("Progression interview requested")),
    (ACTION_PROGRESSION_INTERVIEW_COMPLETED, _("Progression interview completed")),
    (ACTION_PROGRESSION_REJOIN_CREATED, _("Rejoin plan created")),
    (ACTION_PROGRESSION_REJOIN_ACTIVATED, _("Rejoin plan activated")),
    (ACTION_PROGRESSION_REJOIN_CANCELLED, _("Rejoin plan cancelled")),
    # Payments
    (ACTION_PAYMENT_UPLOAD_PREVIEWED, _("Payment upload previewed")),
    (ACTION_PAYMENT_UPLOAD_CONFIRMED, _("Payment upload confirmed")),
    (ACTION_PAYMENT_RECORD_UPDATED, _("Payment record updated")),
    (ACTION_PAYMENT_RECORD_DELETED, _("Payment record deleted")),
    (ACTION_PAYMENT_WAIVER_GRANTED, _("Payment waiver granted")),
    (ACTION_PAYMENT_WAIVER_REVOKED, _("Payment waiver revoked")),
    # Certificates
    (ACTION_CERTIFICATE_GENERATED, _("Certificate generated")),
    (ACTION_CERTIFICATE_REVOKED, _("Certificate revoked")),
    (ACTION_CERTIFICATE_REGENERATED, _("Certificate regenerated")),
    (ACTION_CERTIFICATE_TEMPLATE_DELETED, _("Certificate template deleted")),
    (ACTION_CERTIFICATE_TEMPLATE_CREATED, _("Certificate template created")),
    (ACTION_CERTIFICATE_TEMPLATE_UPDATED, _("Certificate template updated")),
    # Communications
    (ACTION_BULK_EMAIL_SENT, _("Bulk email sent")),
    (ACTION_BULK_EMAIL_DISCARDED, _("Bulk email discarded")),
    (ACTION_COMMUNICATION_TEMPLATE_CREATED, _("Email template created")),
    (ACTION_COMMUNICATION_TEMPLATE_UPDATED, _("Email template updated")),
    (ACTION_COMMUNICATION_FOOTER_UPDATED, _("Email footer updated")),
    # Accounts and access
    (ACTION_USER_IMPORT_PREVIEWED, _("User import previewed")),
    (ACTION_USER_IMPORT_CONFIRMED, _("User import confirmed")),
    (ACTION_USER_CREATED, _("User created")),
    (ACTION_USER_UPDATED, _("User updated")),
    (ACTION_USER_DELETED, _("User deleted")),
    (ACTION_STAFF_ASSIGNMENT_CREATED, _("Staff assignment created")),
    (ACTION_STAFF_ASSIGNMENT_REVOKED, _("Staff assignment revoked")),
    (ACTION_CLUSTER_CREATED, _("Student cluster created")),
    (ACTION_CLUSTER_MEMBERSHIP_ADDED, _("Student added to cluster")),
    (ACTION_CLUSTER_MEMBERSHIP_REMOVED, _("Student removed from cluster")),
    # Settings and system
    (ACTION_SETTINGS_UPDATED, _("Settings updated")),
    (ACTION_EMAIL_CONFIG_CREATED, _("Email configuration created")),
    (ACTION_EMAIL_CONFIG_UPDATED, _("Email configuration updated")),
    (ACTION_EMAIL_CONFIG_ACTIVATED, _("Email configuration activated")),
    (ACTION_EMAIL_CONFIG_DELETED, _("Email configuration deleted")),
    (ACTION_EMAIL_CONFIG_TESTED, _("Email configuration tested")),
    (ACTION_LANDING_UPDATED, _("Landing page updated")),
    (ACTION_ANNOUNCEMENT_UPDATED, _("Landing announcement updated")),
    (ACTION_REDIS_KEY_CREATED, _("Redis key created")),
    (ACTION_REDIS_KEY_DELETED, _("Redis key deleted")),
    (ACTION_SYSTEM_EMAIL_QUEUED, _("System email queued")),
    (ACTION_SYSTEM_EMAIL_SENT, _("System email sent")),
    (ACTION_SYSTEM_EMAIL_FAILED, _("System email failed")),
    (ACTION_SYSTEM_EMAIL_SKIPPED, _("System email skipped")),
    (ACTION_SYSTEM_TASK_STARTED, _("Background job started")),
    (ACTION_SYSTEM_TASK_COMPLETED, _("Background job completed")),
    (ACTION_SYSTEM_TASK_FAILED, _("Background job failed")),
    (ACTION_SYSTEM_TASK_RETRIED, _("Background job retried")),
    (ACTION_ADMIN_CHANGE, _("Django Admin change")),
    (ACTION_REQUEST_MUTATION, _("Page action performed")),
    (ACTION_DEMO_DATA_SEEDED, _("Demo data seeded")),
    # Security
    (ACTION_SECURITY_LOGIN, _("Staff login")),
    (ACTION_SECURITY_LOGOUT, _("Staff logout")),
    (ACTION_SECURITY_LOGIN_FAILED, _("Staff login failed")),
)

# Historic action values that were emitted before the catalog above existed.
# They are rendered with a proper business label but are not rewritten in the
# database: audit history is append-only.
LEGACY_ACTION_LABELS = {
    "exam_completed": _("Entrance exam completed"),
    "application.account_activated": _("Application account activated"),
    "application.entrance_submitted": _("Entrance exam submitted"),
    "application.interview_requested": _("Interview requested"),
    "application.paid": _("Application payment recorded"),
    "academic.diploma_deleted": _("Diploma deleted"),
    "academic.semester_deleted": _("Semester deleted"),
    "academic.course_deleted": _("Course deleted"),
    "academic.course_version_deleted": _("Course version deleted"),
    "academic.lecture_deleted": _("Lecture deleted"),
    "academic.diploma_lecture_deleted": _("Diploma lecture deleted"),
    "academic.cohort_deleted": _("Cohort deleted"),
    "academic.assignment_removed": _("Academic assignment removed"),
    "demo.data_seeded": _("Demo data seeded"),
}

# Outcomes and sources are deliberately small, stable vocabularies so filters
# and badges stay predictable across every app.
SOURCE_USER = "user"
SOURCE_SYSTEM = "system"
SOURCE_TASK = "task"
SOURCE_PUBLIC = "public"
SOURCE_CHOICES = (
    (SOURCE_USER, _("User")),
    (SOURCE_SYSTEM, _("System")),
    (SOURCE_TASK, _("Background job")),
    (SOURCE_PUBLIC, _("Public form")),
)

OUTCOME_SUCCESS = "success"
OUTCOME_FAILURE = "failure"
OUTCOME_DENIED = "denied"
OUTCOME_CHOICES = (
    (OUTCOME_SUCCESS, _("Success")),
    (OUTCOME_FAILURE, _("Failure")),
    (OUTCOME_DENIED, _("Denied")),
)

# Human labels for the target_type values used across the codebase. Historic
# rows use a mix of snake_case and CamelCase model names; this registry keeps
# the audit page readable without rewriting history.
TARGET_TYPE_LABELS = {
    "program_application": _("Application"),
    "ProgramApplication": _("Application"),
    "application": _("Application"),
    "assessment": _("Assessment"),
    "Assessment": _("Assessment"),
    "assessment_attempt": _("Assessment attempt"),
    "AssessmentAttempt": _("Assessment attempt"),
    "reviewer_assignment": _("Reviewer assignment"),
    "ReviewerAssignment": _("Reviewer assignment"),
    "assessment_question": _("Question"),
    "AssessmentQuestion": _("Question"),
    "assessment_submission": _("Assessment submission"),
    "homework_submission": _("Homework submission"),
    "user": _("User"),
    "User": _("User"),
    "student_profile": _("Student"),
    "StudentProfile": _("Student"),
    "payment_record": _("Payment record"),
    "PaymentRecord": _("Payment record"),
    "payment_waiver": _("Payment waiver"),
    "PaymentWaiver": _("Payment waiver"),
    "payment_upload": _("Payment upload"),
    "PaymentUpload": _("Payment upload"),
    "generated_certificate": _("Certificate"),
    "certificate": _("Certificate"),
    "GeneratedCertificate": _("Certificate"),
    "attendance_session": _("Attendance session"),
    "AttendanceSession": _("Attendance session"),
    "attendance_period": _("Attendance period"),
    "attendance_period_day": _("Attendance period day"),
    "diploma_lecture": _("Diploma lecture"),
    "course_enrollment": _("Course enrollment"),
    "diploma_enrollment": _("Diploma enrollment"),
    "teacher_assignment": _("Teacher assignment"),
    "moderator_assignment": _("Moderator assignment"),
    "moderator_student_assignment": _("Moderator student assignment"),
    "student_cluster": _("Student cluster"),
    "student_cluster_membership": _("Student cluster membership"),
    "email_template": _("Email template"),
    "email_footer": _("Email footer"),
    "bulk_email_send": _("Bulk email"),
    "email_config": _("Email configuration"),
    "UserUpload": _("User import"),
    "user_upload": _("User import"),
    "diploma": _("Diploma"),
    "Diploma": _("Diploma"),
    "cohort": _("Cohort"),
    "Cohort": _("Cohort"),
    "semester": _("Semester"),
    "Semester": _("Semester"),
    "course": _("Course"),
    "Course": _("Course"),
    "course_version": _("Course version"),
    "CourseVersion": _("Course version"),
    "lecture": _("Lecture"),
    "Lecture": _("Lecture"),
    "semester_rejoin_plan": _("Rejoin plan"),
    "SemesterRejoinPlan": _("Rejoin plan"),
    "semester_interview": _("Semester interview"),
    "SemesterInterview": _("Semester interview"),
    "certificate_template": _("Certificate template"),
    "CertificateTemplate": _("Certificate template"),
    "payment_upload_row": _("Payment upload row"),
    "PaymentUploadRow": _("Payment upload row"),
    "student_semester_progress": _("Semester progress"),
    "StudentSemesterProgress": _("Semester progress"),
    "email_message": _("Email message"),
    "EmailMessage": _("Email message"),
    "system_task": _("Background job"),
    "redis_key": _("Redis key"),
    "landing_announcement": _("Landing announcement"),
    "settings": _("Settings"),
}

META_KEY_LABELS = {
    "before": _("Before"),
    "after": _("After"),
    "reason": _("Reason"),
    "status_before": _("Status before"),
    "status_after": _("Status after"),
    "role_before": _("Role before"),
    "role_after": _("Role after"),
    "active_before": _("Active status before"),
    "active_after": _("Active status after"),
    "changed_fields": _("Changed fields"),
    "password_reset": _("Password reset"),
    "student_profile_id": _("Student profile ID"),
    "student_id": _("Student ID"),
    "student_name": _("Student"),
    "anonymous_code": _("Anonymous code"),
    "assessment_id": _("Assessment ID"),
    "assessment_name": _("Assessment"),
    "assessment_type": _("Assessment type"),
    "attempt_id": _("Attempt ID"),
    "submission_id": _("Submission ID"),
    "grader_id": _("Grader ID"),
    "grader_name": _("Grader"),
    "grader_role": _("Grader role"),
    "section_id": _("Section ID"),
    "section_title": _("Section"),
    "score_before": _("Score before"),
    "score_after": _("Score after"),
    "max_score": _("Maximum score"),
    "release_state_before": _("Release state before"),
    "release_state_after": _("Release state after"),
    "target": _("Target"),
    "entity_type": _("Entity type"),
    "entity_label": _("Entity"),
    "entity_id": _("Entity ID"),
    "view": _("View"),
    "url_name": _("URL name"),
    "method": _("Method"),
    "status": _("Status"),
    "path": _("Path"),
    "reason_code": _("Reason code"),
    "template_slug": _("Email template"),
    "recipient": _("Recipient"),
    "email_message_id": _("Email ID"),
    "task_id": _("Task ID"),
    "task_name": _("Task"),
    "duration_ms": _("Duration (ms)"),
    "error": _("Error"),
    "key": _("Key"),
    "database": _("Database"),
    "ttl": _("TTL"),
    "value": _("Value"),
    "change_message": _("Change message"),
    "action_flag": _("Admin action"),
    "type": _("Type"),
    "operation": _("Operation"),
    "amount_before": _("Amount before"),
    "amount_after": _("Amount after"),
    "currency": _("Currency"),
    "current_status": _("Current status"),
    "new_status": _("New status"),
    "old_status": _("Previous status"),
    "cohort_before": _("Cohort before"),
    "cohort_after": _("Cohort after"),
    "semester_before": _("Semester before"),
    "semester_after": _("Semester after"),
    "evaluation_id": _("Evaluation ID"),
    "calculated_result": _("Calculated result"),
    "final_result": _("Final result"),
    "overall_percent": _("Overall percent"),
    "attendance_percent": _("Attendance percent"),
    "homework_percent": _("Homework percent"),
    "exam_percent": _("Exam percent"),
    "decision_type": _("Decision type"),
    "exceptional": _("Exceptional decision"),
    "retake_count": _("Retake count"),
    "student_count": _("Student count"),
    "synced": _("Synced"),
    "failed": _("Failed"),
    "component": _("Component"),
    "filename": _("File name"),
    "content_type": _("Content type"),
    "mapping_count": _("Mapped fields"),
    "schema_version": _("Mapping schema version"),
    "program_type": _("Program type"),
    "decision_action": _("Decision action"),
    "application_id": _("Application ID"),
    "action": _("Action"),
    "ok": _("Succeeded"),
    "created": _("Created"),
    "matched": _("Matched"),
    "body_length": _("Body length"),
    "student_user_id": _("Student user ID"),
    "field": _("Field"),
    "delivery_mode_before": _("Delivery mode before"),
    "delivery_mode_after": _("Delivery mode after"),
    "required_before": _("Required before"),
    "required_after": _("Required after"),
    "document_request_id": _("Document request ID"),
    "upload_id": _("Upload ID"),
    "file_count": _("File count"),
    "on_behalf": _("On behalf"),
    "exam_title": _("Entrance exam"),
    "exam_id": _("Entrance exam ID"),
    "score": _("Score"),
    "result": _("Result"),
    "pass_percentage": _("Pass percentage"),
    "attempt_status": _("Attempt status"),
    "graded_answers": _("Graded answers"),
    "claim": _("Claim"),
    "claim_id": _("Claim ID"),
    "stage": _("Stage"),
    "scope": _("Scope"),
    "access_mode": _("Access mode"),
    "code_count": _("Code count"),
    "cohort_id": _("Cohort ID"),
    "section_count": _("Section count"),
    "section_ids": _("Section IDs"),
    "total_before": _("Total before"),
    "total_after": _("Total after"),
    "answers": _("Answers"),
    "reviewed_by_id": _("Reviewed by ID"),
    "reviewed_by_name": _("Reviewed by"),
    "override_id": _("Override ID"),
    "enrollment_id": _("Enrollment ID"),
    "exam_code": _("Exam code"),
    "used_count": _("Used count"),
    "reviewer_id": _("Reviewer ID"),
    "reviewer_name": _("Reviewer"),
    "cluster_id": _("Cluster ID"),
    "semester_id": _("Semester ID"),
    "diploma_id": _("Diploma ID"),
    "diploma_title": _("Diploma"),
    "cohort_name": _("Cohort"),
    "program_title": _("Program"),
    "course_title": _("Course"),
    "form_title": _("Form"),
    "form_url": _("Form URL"),
    "upload_url": _("Upload URL"),
    "expires_at": _("Expires at"),
    "interview_at": _("Interview date"),
    "phone_number": _("Phone number"),
    "staff_phone": _("Staff phone"),
    "rejection_reason": _("Rejection reason"),
    "amount": _("Amount"),
    "reference": _("Reference"),
    "due_at": _("Due date"),
}
