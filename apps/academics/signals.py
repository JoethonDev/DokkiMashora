from __future__ import annotations

from decimal import Decimal

from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from apps.audit.constants import ACTION_ENROLLMENT_DELETED
from apps.audit.services import record_audit_event
from apps.progression.models import DiplomaProgressionRule

from .models import CourseEnrollment, Diploma, DiplomaEnrollment


@receiver(post_save, sender=Diploma, dispatch_uid="auto_create_diploma_progression_rule")
def _auto_create_diploma_progression_rule(sender, instance, created, **kwargs):
    if created:
        DiplomaProgressionRule.objects.get_or_create(
            diploma=instance,
            defaults={
                "is_active": True,
                "semester_pass_percent": Decimal("50.00"),
                "final_exam_pass_percent": Decimal("50.00"),
                "require_all_semesters_passed": True,
                "require_final_exam": False,
                "auto_promote_enabled": False,
                "allow_admin_override": True,
                "allow_retake": True,
                "max_retake_count": 0,
                "created_by": instance.created_by,
            },
        )


def _log_deleted_enrollment(instance, enrollment_type: str, fk_field: str) -> None:
    from apps.payments.models import PaymentRecord

    qs = PaymentRecord.objects.filter(**{fk_field: instance})
    count = qs.count()
    if count == 0:
        count = PaymentRecord.objects.filter(
            target_type=enrollment_type, target_ref=str(instance.id),
        ).count()
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


@receiver(pre_delete, sender=CourseEnrollment, dispatch_uid="log_course_enrollment_deletion")
def _on_course_enrollment_deleted(sender, instance, **kwargs):
    _log_deleted_enrollment(instance, "course", "course_enrollment")


@receiver(pre_delete, sender=DiplomaEnrollment, dispatch_uid="log_diploma_enrollment_deletion")
def _on_diploma_enrollment_deleted(sender, instance, **kwargs):
    _log_deleted_enrollment(instance, "diploma", "diploma_enrollment")
