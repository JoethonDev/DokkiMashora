"""Views for the communications app (bulk email admin)."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.audit.constants import ACTION_BULK_EMAIL_SENT
from apps.audit.services import record_audit_event
from apps.core.permissions import ROLE_SENIOR_STAFF, role_required, safe_int

from .models import BulkEmailSend, EmailTemplate
from .services import create_bulk_draft, has_unresolved_variables, resolve_recipients, send_bulk_email


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def bulk_email_create(request):
    templates = EmailTemplate.objects.filter(is_active=True)
    if request.method == "POST":
        subject = request.POST.get("subject", "").strip()
        body = request.POST.get("body", "").strip()
        template_id = request.POST.get("template_id")
        filter_diploma = request.POST.get("filter_diploma", "").strip()
        filter_cohort = request.POST.get("filter_cohort", "").strip()
        filter_status = request.POST.get("filter_status", "").strip()
        manual_emails = request.POST.get("manual_emails", "").strip()

        if not subject or not body:
            messages.error(request, _("Subject and body are required."))
            return render(request, "communications/bulk_email_create.html", {
                "templates": templates,
                "subject": subject,
                "body": body,
                "template_id": template_id,
                "filter_diploma": filter_diploma,
                "filter_cohort": filter_cohort,
                "filter_status": filter_status,
                "manual_emails": manual_emails,
            })

        filter_criteria = {}
        diploma_val = safe_int(filter_diploma)
        if diploma_val is not None:
            filter_criteria["diploma_id"] = diploma_val
        cohort_val = safe_int(filter_cohort)
        if cohort_val is not None:
            filter_criteria["cohort_id"] = cohort_val
        if filter_status:
            filter_criteria["status"] = filter_status

        request.session["bulk_email_draft"] = {
            "subject": subject,
            "body": body,
            "template_id": template_id,
            "filter_criteria": filter_criteria,
            "manual_emails": manual_emails,
        }
        return redirect("communications:bulk_email_preview")

    return render(request, "communications/bulk_email_create.html", {
        "templates": templates,
    })


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def bulk_email_preview(request):
    draft = request.session.get("bulk_email_draft")
    if not draft:
        messages.info(request, _("No draft found. Start a new bulk email."))
        return redirect("communications:bulk_email_create")

    recipients = resolve_recipients(
        filter_criteria=draft.get("filter_criteria"),
        manual_emails=draft.get("manual_emails", ""),
    )

    if request.method == "POST":
        if not recipients:
            messages.error(request, _("No recipients resolved."))
            return redirect("communications:bulk_email_create")

        if has_unresolved_variables(draft["subject"]) or has_unresolved_variables(draft["body"]):
            messages.error(
                request,
                _("Email contains unresolved template variables (e.g. {{ variable }}). Resolve them before sending."),
            )
            return render(request, "communications/bulk_email_preview.html", {
                "draft": draft,
                "recipients": recipients,
                "recipient_count": len(recipients),
            })

        bulk_send = create_bulk_draft(
            subject=draft["subject"],
            body=draft["body"],
            template_id=draft.get("template_id"),
            filter_criteria=draft.get("filter_criteria"),
            manual_emails=draft.get("manual_emails", ""),
            sent_by=request.user,
        )
        send_bulk_email(bulk_send)

        record_audit_event(
            actor=request.user,
            action=ACTION_BULK_EMAIL_SENT,
            target_type="bulk_email_send",
            target_id=str(bulk_send.id),
            metadata={
                "subject": draft["subject"],
                "recipient_count": len(recipients),
                "success_count": bulk_send.success_count,
                "failure_count": bulk_send.failure_count,
            },
        )

        del request.session["bulk_email_draft"]
        messages.success(
            request,
            _("Bulk email sent to %(count)d recipients (%(success)d succeeded, %(failure)d failed).")
            % {"count": len(recipients), "success": bulk_send.success_count, "failure": bulk_send.failure_count},
        )
        return redirect("communications:bulk_email_history")

    return render(request, "communications/bulk_email_preview.html", {
        "draft": draft,
        "recipients": recipients,
        "recipient_count": len(recipients),
    })


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def bulk_email_history(request):
    sends = BulkEmailSend.objects.filter(sent_by=request.user).select_related("template")
    return render(request, "communications/bulk_email_history.html", {
        "sends": sends,
    })


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def bulk_email_detail(request, send_id):
    send = get_object_or_404(
        BulkEmailSend.objects.select_related("template", "sent_by"),
        id=send_id,
    )
    messages_list = send.messages.select_related("sent_by").all()
    return render(request, "communications/bulk_email_detail.html", {
        "send": send,
        "messages": messages_list,
    })


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def bulk_email_discard(request):
    if "bulk_email_draft" in request.session:
        del request.session["bulk_email_draft"]
        messages.info(request, _("Draft discarded."))
    return redirect("communications:bulk_email_create")
