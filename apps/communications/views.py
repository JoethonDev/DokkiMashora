"""Views for the communications app (bulk email admin)."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods, require_POST

from apps.audit.constants import ACTION_BULK_EMAIL_SENT
from apps.audit.services import record_audit_event
from apps.core.pagination import paginate
from apps.core.permissions import ROLE_SENIOR_STAFF, role_required, safe_int

from .constants import REQUIRED_VARIABLES_BY_SLUG, SAMPLE_CONTEXT_BY_SLUG, TEMPLATE_SLUGS
from .models import BulkEmailSend, EmailTemplate
from .services import create_bulk_draft, get_global_footer, has_unresolved_variables, resolve_recipients, send_bulk_email


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
    page_obj, pagination_qs = paginate(request, sends)
    return render(request, "communications/bulk_email_history.html", {
        "page_obj": page_obj,
        "pagination_qs": pagination_qs,
    })


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def bulk_email_detail(request, send_id):
    send = get_object_or_404(
        BulkEmailSend.objects.select_related("template", "sent_by"),
        id=send_id,
    )
    messages_list = send.messages.select_related("sent_by").all()
    page_obj, pagination_qs = paginate(request, messages_list)
    return render(request, "communications/bulk_email_detail.html", {
        "send": send,
        "page_obj": page_obj,
        "pagination_qs": pagination_qs,
    })


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def bulk_email_discard(request):
    if "bulk_email_draft" in request.session:
        del request.session["bulk_email_draft"]
        messages.info(request, _("Draft discarded."))
    return redirect("communications:bulk_email_create")


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_template_list(request):
    """List all EmailTemplate records with preview links."""
    templates = EmailTemplate.objects.all().order_by("slug")
    from .models import EmailFooter
    footer = EmailFooter.objects.first()
    return render(request, "communications/admin_template_list.html", {
        "templates": templates,
        "global_footer": footer.body if footer else "",
    })


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_http_methods(["GET", "POST"])
def admin_template_edit(request, slug):
    """Edit an EmailTemplate subject, body, is_enabled_for_action."""
    template = get_object_or_404(EmailTemplate, slug=slug)
    if request.method == "POST":
        subject = request.POST.get("subject", "").strip()
        body = request.POST.get("body", "").strip()
        enabled = request.POST.get("is_enabled_for_action") == "on"
        if not subject:
            messages.error(request, _("Subject is required."))
        elif not body:
            messages.error(request, _("Body is required."))
        else:
            template.subject = subject
            template.body = body
            template.is_enabled_for_action = enabled
            try:
                template.full_clean()
                template.save(update_fields=["subject", "body", "is_enabled_for_action"])
                messages.success(request, _("Template updated."))
                return redirect("communications:admin_template_list")
            except ValidationError as e:
                for field, errors in e.message_dict.items():
                    for err in errors:
                        messages.error(request, f"{field}: {err}")
    return render(request, "communications/admin_template_form.html", {
        "template": template,
        "required_vars": REQUIRED_VARIABLES_BY_SLUG.get(slug, []),
        "all_slugs": TEMPLATE_SLUGS,
    })


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_template_preview(request, slug):
    """Preview an EmailTemplate rendered with sample context."""
    template = get_object_or_404(EmailTemplate, slug=slug)
    context = SAMPLE_CONTEXT_BY_SLUG.get(slug, {})
    try:
        rendered_subject, rendered_body = template.render(context)
    except Exception:
        rendered_subject, rendered_body = template.subject, template.body
    design = request.GET.get("design", "")
    footer_text = get_global_footer()
    rendered_html = ""
    if design in ("basic", "branded", "minimal"):
        rendered_html = render_to_string(
            f"communications/email_designs/{design}.html",
            {"content": rendered_body, "footer": footer_text},
            request=request,
        )
    return render(request, "communications/admin_template_preview.html", {
        "template": template,
        "rendered_subject": rendered_subject,
        "rendered_body": rendered_body,
        "rendered_html": rendered_html,
        "selected_design": design,
        "designs": ["basic", "branded", "minimal"],
        "sample_context": context,
        "missing": [v for v in template.variable_names() if v not in context],
    })


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_http_methods(["GET", "POST"])
def admin_template_create(request):
    """Create a new EmailTemplate for a chosen slug/action."""
    used_slugs = set(EmailTemplate.objects.values_list("slug", flat=True))
    available = [s for s in TEMPLATE_SLUGS if s not in used_slugs]
    if request.method == "POST":
        slug = request.POST.get("slug", "").strip()
        name = request.POST.get("name", "").strip()
        subject = request.POST.get("subject", "").strip()
        body = request.POST.get("body", "").strip()
        enabled = request.POST.get("is_enabled_for_action") == "on"
        if slug not in TEMPLATE_SLUGS:
            messages.error(request, _("Invalid action."))
        elif slug in used_slugs:
            messages.error(request, _("A template for this action already exists."))
        elif not name:
            messages.error(request, _("Name is required."))
        elif not subject:
            messages.error(request, _("Subject is required."))
        elif not body:
            messages.error(request, _("Body is required."))
        else:
            template = EmailTemplate(
                slug=slug, name=name, subject=subject, body=body,
                is_enabled_for_action=enabled,
            )
            try:
                template.full_clean()
                template.save()
                messages.success(request, _("Template created."))
                return redirect("communications:admin_template_list")
            except ValidationError as e:
                for field, errors in e.message_dict.items():
                    for err in errors:
                        messages.error(request, f"{field}: {err}")
    return render(request, "communications/admin_template_create.html", {
        "available_slugs": available,
        "all_slugs": TEMPLATE_SLUGS,
        "required_vars_by_slug": REQUIRED_VARIABLES_BY_SLUG,
    })


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_http_methods(["GET", "POST"])
def admin_footer_edit(request):
    """Edit the global email footer."""
    from .models import EmailFooter
    footer = EmailFooter.objects.first()
    if not footer:
        footer = EmailFooter.objects.create(body="")
    if request.method == "POST":
        body = request.POST.get("body", "").strip()
        footer.body = body
        footer.save(update_fields=["body"])
        messages.success(request, _("Global footer updated."))
        return redirect("communications:admin_template_list")
    return render(request, "communications/admin_footer_form.html", {
        "footer": footer,
    })
