from __future__ import annotations

from django.db.models import Exists, OuterRef, Prefetch, Q, QuerySet, Sum

from apps.academics.models import CourseEnrollment, DiplomaEnrollment
from apps.students.models import StudentProfile

from .constants import PAYMENT_STATUS_PAID
from .models import PaymentRecord, PaymentUpload, PaymentUploadRow


def get_upload_queryset(
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> QuerySet[PaymentUpload]:
    qs = PaymentUpload.objects.select_related("uploaded_by").all()
    if status:
        qs = qs.filter(status=status)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    return qs


def get_upload_detail(upload_id: int) -> PaymentUpload | None:
    return (
        PaymentUpload.objects.select_related("uploaded_by")
        .prefetch_related(
            Prefetch(
                "rows",
                queryset=PaymentUploadRow.objects.select_related("matched_student__user"),
            )
        )
        .filter(id=upload_id)
        .first()
    )


def get_student_payment_records(student: StudentProfile) -> QuerySet[PaymentRecord]:
    return PaymentRecord.objects.filter(student=student, deleted_at__isnull=True).select_related(
        "student__user", "upload_row"
    ).order_by("-paid_at", "-created_at")


def get_student_payment_summary(student: StudentProfile) -> dict:
    total_paid = (
        PaymentRecord.objects.filter(student=student, status=PAYMENT_STATUS_PAID)
        .aggregate(total=Sum("amount"))["total"]
        or 0
    )
    records = list(get_student_payment_records(student))
    return {
        "total_paid": total_paid,
        "record_count": len(records),
        "records": records,
    }


def get_enrollment_payment_status(student: StudentProfile) -> dict:
    """Return diploma + course enrollments annotated with has_paid."""
    paid = PaymentRecord.objects.filter(
        status=PAYMENT_STATUS_PAID, student=student,
        diploma_enrollment=OuterRef("pk"),
    )
    diploma_qs = (
        DiplomaEnrollment.objects.filter(student=student)
        .annotate(has_paid=Exists(paid))
        .select_related("cohort__diploma")
    )
    paid = PaymentRecord.objects.filter(
        status=PAYMENT_STATUS_PAID, student=student,
        course_enrollment=OuterRef("pk"),
    )
    course_qs = (
        CourseEnrollment.objects.filter(student=student)
        .annotate(has_paid=Exists(paid))
        .select_related("course_version__course")
    )
    return {
        "diploma_enrollments": list(diploma_qs),
        "course_enrollments": list(course_qs),
    }


def get_payment_record_queryset(
    status: str | None = None,
    student_q: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> QuerySet[PaymentRecord]:
    qs = PaymentRecord.objects.select_related(
        "student__user",
        "upload_row",
        "application__cohort__diploma",
        "application__course",
        "diploma_enrollment__cohort__diploma",
        "course_enrollment__course_version__course",
    ).filter(deleted_at__isnull=True)
    if status:
        qs = qs.filter(status=status)
    if student_q:
        qs = qs.filter(
            Q(student__user__full_name__icontains=student_q)
            | Q(student__user__email__icontains=student_q)
            | Q(student__national_id__icontains=student_q)
            | Q(student__phone__icontains=student_q)
        )
    if date_from:
        qs = qs.filter(paid_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(paid_at__date__lte=date_to)
    return qs
