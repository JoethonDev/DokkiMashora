from __future__ import annotations

from django.db.models.signals import post_delete
from django.dispatch import receiver

from apps.audit.constants import ACTION_ENROLLMENT_DELETED
from apps.audit.services import record_audit_event

from .models import CourseEnrollment, DiplomaEnrollment


def _log_deleted_enrollment(instance, enrollment_type: str, fk_field: str) -> None:
    from apps.payments.models import PaymentRecord

    count = PaymentRecord.objects.filter(**{fk_field: instance}).count()
    if count > 0:
        record_audit_event(
            actor=None,
            action=ACTION_ENROLLMENT_DELETED,
            target_type=f"{enrollment_type}_enrollment",
            target_id=str(instance.id),
            metadata={
                "enrollment_type": enrollment_type,
                "payment_records_affected": count,
            },
        )


@receiver(post_delete, sender=CourseEnrollment, dispatch_uid="log_course_enrollment_deletion")
def _on_course_enrollment_deleted(sender, instance, **kwargs):
    _log_deleted_enrollment(instance, "course", "course_enrollment")


@receiver(post_delete, sender=DiplomaEnrollment, dispatch_uid="log_diploma_enrollment_deletion")
def _on_diploma_enrollment_deleted(sender, instance, **kwargs):
    _log_deleted_enrollment(instance, "diploma", "diploma_enrollment")
