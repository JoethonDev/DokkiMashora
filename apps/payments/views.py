from __future__ import annotations

import csv
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.accounts.constants import GROUP_ADMIN, GROUP_SUPER_ADMIN
from apps.audit.constants import (
    ACTION_PAYMENT_UPLOAD_CONFIRMED,
    ACTION_PAYMENT_UPLOAD_PREVIEWED,
)
from apps.audit.services import record_audit_event
from apps.communications.constants import TEMPLATE_PAYMENT_NOTIFICATION
from apps.communications.services import queue_templated_email
from apps.core.htmx import is_htmx
from apps.core.pagination import paginate
from apps.core.permissions import role_required, safe_date_str
from apps.core.views import safe_next_url
from apps.students.models import StudentProfile

from .constants import (
    PAYMENT_STATUS_CHOICES,
    UPLOAD_STATUS_CHOICES,
    UPLOAD_STATUS_FAILED,
    UPLOAD_STATUS_PREVIEWED,
    WAIVER_STATUS_CHOICES,
)
from .forms import PaymentRecordEditForm, PaymentUploadForm
from .models import PaymentRecord, PaymentUpload
from .selectors import (
    get_payment_record_queryset,
    get_upload_detail,
    get_upload_queryset,
    get_waiver_queryset,
)
from .services import (
    confirm_upload,
    create_upload,
    mark_enrollment_paid,
    mark_enrollment_unpaid,
    preview_upload,
    reconcile_payment_record,
)

ROLE_PAYMENT_STAFF = (GROUP_SUPER_ADMIN, GROUP_ADMIN)
logger = logging.getLogger(__name__)


@login_required
@role_required(*ROLE_PAYMENT_STAFF)
def upload_list(request):
    status = request.GET.get("status", "")
    row_status = request.GET.get("row_status", "")
    raw_date_from = request.GET.get("date_from", "")
    raw_date_to = request.GET.get("date_to", "")
    date_from = safe_date_str(raw_date_from)
    date_to = safe_date_str(raw_date_to)
    uploads = get_upload_queryset(
        status=status or None,
        row_status=row_status or None,
        date_from=date_from,
        date_to=date_to,
    )
    page_obj, pagination_qs = paginate(request, uploads)
    template = "ui/fragments/admin/_payment_upload_table.html" if is_htmx(request) else "ui/pages/admin/payments.html"
    return render(
        request,
        template,
        {
            "page_mode": "upload_list",
            "page_obj": page_obj,
            "pagination_qs": pagination_qs,
            "upload_status_choices": UPLOAD_STATUS_CHOICES,
            "current_status": status,
            "current_row_status": row_status,
            "current_date_from": raw_date_from,
            "current_date_to": raw_date_to,
        },
    )


@login_required
@role_required(*ROLE_PAYMENT_STAFF)
def download_payment_csv_template(request):
    headers = ["national_id", "amount", "payment_date", "external_reference", "email", "phone", "notes", "application_id", "diploma_enrollment_id", "course_enrollment_id", "target_type", "target_ref"]
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="payment_template.csv"'
    writer = csv.writer(response)
    writer.writerow(headers)
    writer.writerow(["29001010100001", "500.00", "2026-01-15", "INV-001", "student@example.com", "+201234567890", _("Tuition fee"), "", "", "", "diploma", "42"])
    writer.writerow(["", "500.00", "2026-01-15", "INV-EMAIL-ONLY", "student@example.com", "", _("Email-only identifier example"), "", "", "", "", ""])
    return response


@login_required
@role_required(*ROLE_PAYMENT_STAFF)
def upload_create(request):
    form = PaymentUploadForm()
    if request.method == "POST":
        form = PaymentUploadForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                content = request.FILES["csv_file"].read()
                upload = create_upload(
                    original_filename=request.FILES["csv_file"].name,
                    content=content,
                    uploaded_by=request.user,
                )
                upload = preview_upload(upload.id)
                if upload.status == UPLOAD_STATUS_PREVIEWED:
                    messages.success(
                        request,
                        _("File parsed successfully. Review the preview below."),
                    )
                else:
                    messages.warning(
                        request,
                        _(
                            "Some rows could not be matched. "
                            "Review the errors below and fix the file."
                        ),
                    )
                record_audit_event(
                    actor=request.user,
                    action=ACTION_PAYMENT_UPLOAD_PREVIEWED,
                    target_type="PaymentUpload",
                    target_id=str(upload.id),
                    metadata={
                        "filename": upload.original_filename,
                        "total_rows": upload.total_rows,
                        "matched_rows": upload.matched_rows,
                        "failed_rows": upload.failed_rows,
                    },
                )
                return redirect("payments:upload_detail", upload_id=upload.id)
            except ValueError as e:
                messages.error(request, str(e))
            except Exception:
                logger.exception("Payment upload processing failed for %s", request.FILES["csv_file"].name)
                messages.error(request, _("The file could not be processed. Check the format and try again."))
    return render(request, "ui/pages/admin/payments.html", {"page_mode": "upload_create", "form": form, "title": _("Payment upload")})


@login_required
@role_required(*ROLE_PAYMENT_STAFF)
def upload_detail(request, upload_id):
    upload = get_upload_detail(upload_id)
    if not upload:
        return redirect("payments:upload_list")
    return render(request, "ui/pages/admin/payments.html", {"page_mode": "upload_detail", "upload": upload, "title": _("Payment upload")})


@require_POST
@login_required
@role_required(*ROLE_PAYMENT_STAFF)
def upload_confirm(request, upload_id):
    upload = get_object_or_404(PaymentUpload, id=upload_id)
    if upload.status != UPLOAD_STATUS_PREVIEWED:
        messages.error(request, _("Upload is not in preview state."))
        return redirect("payments:upload_detail", upload_id=upload.id)
    try:
        upload = confirm_upload(upload.id, confirmed_by=request.user)
        if upload.status == UPLOAD_STATUS_FAILED:
            messages.error(
                request,
                _("Import rejected: %(count)d row(s) failed. No payments were applied.")
                % {"count": upload.failed_rows},
            )
        else:
            messages.success(
                request,
                _(
                    "Import completed: %(created)d created, %(updated)d updated, "
                    "%(failed)d failed."
                )
                % {
                    "created": upload.created_rows,
                    "updated": upload.updated_rows,
                    "failed": upload.failed_rows,
                },
            )
        record_audit_event(
            actor=request.user,
            action=ACTION_PAYMENT_UPLOAD_CONFIRMED,
            target_type="PaymentUpload",
            target_id=str(upload.id),
            metadata={
                "filename": upload.original_filename,
                "created_rows": upload.created_rows,
                "updated_rows": upload.updated_rows,
                "failed_rows": upload.failed_rows,
            },
        )
    except ValueError as e:
        messages.error(request, str(e))
    return redirect("payments:upload_detail", upload_id=upload.id)


@login_required
@role_required(*ROLE_PAYMENT_STAFF)
def payment_record_list(request):
    status = request.GET.get("status", "")
    student_q = request.GET.get("student_q", "")
    raw_date_from = request.GET.get("date_from", "")
    raw_date_to = request.GET.get("date_to", "")
    date_from = safe_date_str(raw_date_from)
    date_to = safe_date_str(raw_date_to)
    records = get_payment_record_queryset(
        status=status or None,
        student_q=student_q or None,
        date_from=date_from,
        date_to=date_to,
    )
    page_obj, pagination_qs = paginate(request, records)
    template = "ui/fragments/admin/_payment_record_table.html" if is_htmx(request) else "ui/pages/admin/payments.html"
    return render(
        request,
        template,
        {
            "page_mode": "record_list",
            "page_obj": page_obj,
            "pagination_qs": pagination_qs,
            "payment_status_choices": PAYMENT_STATUS_CHOICES,
            "current_status": status,
            "current_student_q": student_q,
            "current_date_from": raw_date_from,
            "current_date_to": raw_date_to,
        },
    )


@login_required
@role_required(*ROLE_PAYMENT_STAFF)
def payment_record_detail(request, record_id):
    record = get_object_or_404(
        PaymentRecord.objects.select_related(
            "student__user", "upload_row__upload", "application__cohort__diploma",
            "application__course", "imported_by",
            "diploma_enrollment__cohort__diploma",
            "course_enrollment__course_version__course",
        ),
        id=record_id,
    )
    return render(request, "ui/pages/admin/payments.html", {"page_mode": "record_detail", "record": record, "title": _("Payment record")})


@login_required
@role_required(*ROLE_PAYMENT_STAFF)
@transaction.atomic
def payment_record_edit(request, record_id):
    record = get_object_or_404(
        PaymentRecord.objects.select_related("student__user"),
        id=record_id,
        deleted_at__isnull=True,
    )
    form = PaymentRecordEditForm(request.POST or None, instance=record)
    if request.method == "POST" and form.is_valid():
        try:
            reconcile_payment_record(
                actor=request.user,
                record_id=record.id,
                student=record.student,
                application=record.application,
                amount=form.cleaned_data["amount"],
                status=form.cleaned_data["status"],
                paid_at=form.cleaned_data.get("paid_at"),
                due_at=form.cleaned_data.get("due_at"),
                external_reference=form.cleaned_data.get("external_reference", ""),
                notes=form.cleaned_data.get("notes", ""),
                reason=(request.POST.get("reason") or "").strip(),
                source="manual",
            )
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Payment record updated."))
            return redirect("payments:record_detail", record_id=record.id)
    return render(request, "ui/pages/admin/payments.html", {"page_mode": "record_edit", "form": form, "record": record, "title": _("Payment record")})


@require_POST
@login_required
@role_required(*ROLE_PAYMENT_STAFF)
def enrollment_payment_toggle(request, student_id, enrollment_type, enrollment_id):
    """Mark an enrollment paid or unpaid."""
    student = get_object_or_404(StudentProfile, pk=student_id)
    action = request.POST.get("payment_action")
    if action == "mark_paid":
        record = mark_enrollment_paid(student, enrollment_type, enrollment_id, actor=request.user)
        transaction.on_commit(
            lambda: queue_templated_email(
                TEMPLATE_PAYMENT_NOTIFICATION,
                student.user.email,
                {
                    "full_name": student.full_name,
                    "amount": str(record.amount),
                    "currency": record.currency,
                    "due_at": record.due_at.strftime("%Y-%m-%d") if record.due_at else record.paid_at.strftime("%Y-%m-%d") if record.paid_at else "",
                    "reference": record.external_reference or str(record.id),
                    "status": record.get_status_display(),
                },
                sent_by=request.user,
                idempotency_key=f"payment:notification:record:{record.pk}",
                reference=f"payment_record:{record.pk}",
            )
        )
        messages.success(request, _("Enrollment marked as paid. Payment notification email queued."))
    elif action == "mark_unpaid":
        reason = request.POST.get("reason", "").strip() or _("Marked unpaid from the enrollment payment toggle")
        count = mark_enrollment_unpaid(student, enrollment_type, enrollment_id, actor=request.user, reason=reason)
        if count:
            messages.info(request, _("Enrollment marked as unpaid."))
        else:
            messages.warning(request, _("No paid records to revert."))
    else:
        messages.error(request, _("Invalid action."))
    return redirect(safe_next_url(request, default="payments:record_list"))


@require_POST
@login_required
@role_required(*ROLE_PAYMENT_STAFF)
@transaction.atomic
def payment_record_delete(request, record_id):
    record = get_object_or_404(
        PaymentRecord.objects.select_related("student", "application"),
        id=record_id,
        deleted_at__isnull=True,
    )
    try:
        reconcile_payment_record(
            actor=request.user,
            record_id=record.id,
            student=record.student,
            application=record.application,
            amount=record.amount,
            status=record.status,
            paid_at=record.paid_at,
            due_at=record.due_at,
            external_reference=record.external_reference,
            notes=record.notes,
            reason=(request.POST.get("reason") or "").strip(),
            source="manual",
            soft_delete=True,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("Payment record deleted."))
    return redirect("payments:record_list")


@login_required
@role_required(*ROLE_PAYMENT_STAFF)
def waiver_list(request):
    status = request.GET.get("status", "")
    student_q = request.GET.get("student_q", "")
    waivers = get_waiver_queryset(status=status or None, student_q=student_q or None)
    page_obj, pagination_qs = paginate(request, waivers)
    template = "ui/fragments/admin/_payment_waiver_table.html" if is_htmx(request) else "ui/pages/admin/payments.html"
    return render(
        request,
        template,
        {
            "page_mode": "waiver_list",
            "page_obj": page_obj,
            "pagination_qs": pagination_qs,
            "waiver_status_choices": WAIVER_STATUS_CHOICES,
            "current_status": status,
            "current_student_q": student_q,
        },
    )
