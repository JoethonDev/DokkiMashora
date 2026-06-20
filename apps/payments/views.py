from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.accounts.constants import GROUP_ADMIN, GROUP_MODERATOR, GROUP_SUPER_ADMIN
from apps.audit.constants import ACTION_PAYMENT_UPLOAD_CONFIRMED, ACTION_PAYMENT_UPLOAD_PREVIEWED
from apps.audit.services import record_audit_event
from apps.core.permissions import role_required, safe_date_str

from .constants import PAYMENT_STATUS_CHOICES, UPLOAD_STATUS_CHOICES, UPLOAD_STATUS_PREVIEWED
from .forms import PaymentUploadForm
from .models import PaymentRecord, PaymentUpload
from .selectors import (
    get_payment_record_queryset,
    get_upload_detail,
    get_upload_queryset,
)
from .services import confirm_upload, create_upload, preview_upload

ROLE_PAYMENT_STAFF = (GROUP_SUPER_ADMIN, GROUP_ADMIN, GROUP_MODERATOR)


@login_required
@role_required(*ROLE_PAYMENT_STAFF)
def upload_list(request):
    status = request.GET.get("status", "")
    raw_date_from = request.GET.get("date_from", "")
    raw_date_to = request.GET.get("date_to", "")
    date_from = safe_date_str(raw_date_from)
    date_to = safe_date_str(raw_date_to)
    uploads = get_upload_queryset(
        status=status or None,
        date_from=date_from,
        date_to=date_to,
    )
    return render(
        request,
        "payments/admin_upload_list.html",
        {
            "uploads": uploads,
            "upload_status_choices": UPLOAD_STATUS_CHOICES,
            "current_status": status,
            "current_date_from": raw_date_from,
            "current_date_to": raw_date_to,
        },
    )


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
            except Exception as e:
                messages.error(request, _("Failed to process file: %(error)s") % {"error": str(e)})
    return render(request, "payments/admin_upload_form.html", {"form": form})


@login_required
@role_required(*ROLE_PAYMENT_STAFF)
def upload_detail(request, upload_id):
    upload = get_upload_detail(upload_id)
    if not upload:
        return redirect("payments:upload_list")
    return render(request, "payments/admin_upload_detail.html", {"upload": upload})


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
    return render(
        request,
        "payments/admin_record_list.html",
        {
            "records": records,
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
            "student__user", "upload_row__upload", "application", "imported_by"
        ),
        id=record_id,
    )
    return render(request, "payments/admin_record_detail.html", {"record": record})
