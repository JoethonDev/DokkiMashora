from __future__ import annotations

from django.utils.translation import gettext_lazy as _

ACTION_APPLICATION_STATUS_CHANGE = "application.status_change"
ACTION_APPLICATION_APPROVED = "application.approved"
ACTION_APPLICATION_REJECTED = "application.rejected"
ACTION_APPLICATION_ACTIVATED = "application.activated"
ACTION_STUDENT_ACCESS_CHANGE = "student.access_change"
ACTION_ENROLLMENT_CHANGE = "enrollment.change"
ACTION_ENROLLMENT_DELETED = "enrollment.deleted"
ACTION_EXAM_CREATED = "exam.created"
ACTION_EXAM_UPDATED = "exam.updated"
ACTION_EXAM_TOGGLED = "exam.toggled"
ACTION_QUESTION_CREATED = "question.created"
ACTION_QUESTION_UPDATED = "question.updated"
ACTION_QUESTION_DELETED = "question.deleted"
ACTION_GRADE_CHANGED = "grade.changed"
ACTION_GRADE_OVERRIDE = "grade.override"
ACTION_GRADE_RELEASED = "grade.released"
ACTION_GRADE_HIDDEN = "grade.hidden"
ACTION_PROMOTION = "promotion.promoted"
ACTION_DEMOTION = "promotion.demoted"
ACTION_LOCK = "promotion.locked"
ACTION_UNLOCK = "promotion.unlocked"
ACTION_RETAKE = "promotion.retake"
ACTION_CERTIFICATE_GENERATED = "certificate.generated"
ACTION_CERTIFICATE_REVOKED = "certificate.revoked"
ACTION_CERTIFICATE_REGENERATED = "certificate.regenerated"
ACTION_BULK_EMAIL_SENT = "bulk_email.sent"
ACTION_APPLICATION_DECISION_REVERTED = "application.decision_reverted"
ACTION_PAYMENT_UPLOAD_PREVIEWED = "payment.upload_previewed"
ACTION_PAYMENT_UPLOAD_CONFIRMED = "payment.upload_confirmed"
ACTION_PAYMENT_RECORD_UPDATED = "payment.record_updated"
ACTION_PAYMENT_RECORD_DELETED = "payment.record_deleted"
ACTION_USER_IMPORT_PREVIEWED = "user_import.previewed"
ACTION_USER_IMPORT_CONFIRMED = "user_import.confirmed"

AUDIT_ACTION_CHOICES = (
    (ACTION_APPLICATION_STATUS_CHANGE, _("Application status change")),
    (ACTION_APPLICATION_APPROVED, _("Application approved")),
    (ACTION_APPLICATION_REJECTED, _("Application rejected")),
    (ACTION_APPLICATION_ACTIVATED, _("Application activated")),
    (ACTION_APPLICATION_DECISION_REVERTED, _("Application decision reverted")),
    (ACTION_STUDENT_ACCESS_CHANGE, _("Student access change")),
    (ACTION_ENROLLMENT_CHANGE, _("Enrollment change")),
    (ACTION_ENROLLMENT_DELETED, _("Enrollment deleted")),
    (ACTION_EXAM_CREATED, _("Exam created")),
    (ACTION_EXAM_UPDATED, _("Exam updated")),
    (ACTION_EXAM_TOGGLED, _("Exam toggled")),
    (ACTION_QUESTION_CREATED, _("Question created")),
    (ACTION_QUESTION_UPDATED, _("Question updated")),
    (ACTION_QUESTION_DELETED, _("Question deleted")),
    (ACTION_GRADE_CHANGED, _("Grade changed")),
    (ACTION_GRADE_OVERRIDE, _("Grade override")),
    (ACTION_GRADE_RELEASED, _("Grade released")),
    (ACTION_GRADE_HIDDEN, _("Grade hidden")),
    (ACTION_PROMOTION, _("Promotion")),
    (ACTION_DEMOTION, _("Demotion")),
    (ACTION_LOCK, _("Lock")),
    (ACTION_UNLOCK, _("Unlock")),
    (ACTION_RETAKE, _("Retake")),
    (ACTION_CERTIFICATE_GENERATED, _("Certificate generated")),
    (ACTION_CERTIFICATE_REVOKED, _("Certificate revoked")),
    (ACTION_CERTIFICATE_REGENERATED, _("Certificate regenerated")),
    (ACTION_BULK_EMAIL_SENT, _("Bulk email sent")),
    (ACTION_PAYMENT_UPLOAD_PREVIEWED, _("Payment upload previewed")),
    (ACTION_PAYMENT_UPLOAD_CONFIRMED, _("Payment upload confirmed")),
    (ACTION_PAYMENT_RECORD_UPDATED, _("Payment record updated")),
    (ACTION_PAYMENT_RECORD_DELETED, _("Payment record deleted")),
    (ACTION_USER_IMPORT_PREVIEWED, _("User import previewed")),
    (ACTION_USER_IMPORT_CONFIRMED, _("User import confirmed")),
)
