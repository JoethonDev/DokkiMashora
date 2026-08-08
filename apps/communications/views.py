"""Views for the communications app (bulk email admin)."""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import escape
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods, require_POST

from apps.academics.constants import ENROLLMENT_STATUS_CHOICES
from apps.academics.models import Cohort, Diploma
from apps.audit.constants import ACTION_BULK_EMAIL_SENT
from apps.audit.services import record_audit_event
from apps.core.pagination import paginate
from apps.core.permissions import ROLE_SENIOR_STAFF, role_required, safe_int

from .constants import (
    ALL_VARIABLE_SAMPLES,
    AVAILABLE_VARIABLES_BY_SLUG,
    BULK_STATUS_FAILED,
    DESIGN_BRANDED,
    DESIGN_CHOICES,
    SAMPLE_CONTEXT_BY_SLUG,
    TEMPLATE_SLUGS,
)
from .models import BulkEmailSend, EmailFooter, EmailTemplate
from .services import (
    _wrap_in_design,
    create_bulk_draft,
    enqueue_bulk_email_send,
    get_global_footer,
    has_unresolved_variables,
    resolve_recipients,
)

logger = logging.getLogger("apps.communications.views")


def _bulk_email_form_context():
    return {
        "diplomas": Diploma.objects.filter(is_active=True).order_by("title"),
        "cohorts": Cohort.objects.filter(is_active=True).select_related("diploma").order_by("-start_date"),
        "status_choices": ENROLLMENT_STATUS_CHOICES,
    }



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
        recipient_search = request.POST.get("recipient_search", "").strip()
        manual_emails = request.POST.get("manual_emails", "").strip()

        if not subject or not body:
            ctx = {"templates": templates, "subject": subject, "body": body,
                   "template_id": template_id, "filter_diploma": filter_diploma,
                   "filter_cohort": filter_cohort, "filter_status": filter_status,
                   "recipient_search": recipient_search, "manual_emails": manual_emails}
            ctx.update(_bulk_email_form_context())
            messages.error(request, _("Subject and body are required."))
            return render(request, "ui/pages/admin/communication_bulk_form.html", {**ctx, "bulk_form": True, "title": _("Create bulk email")})

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
            "recipient_search": recipient_search,
            "manual_emails": manual_emails,
        }
        return redirect("communications:bulk_email_preview")

    ctx = {"templates": templates}
    ctx.update(_bulk_email_form_context())
    return render(request, "ui/pages/admin/communication_bulk_form.html", {**ctx, "bulk_form": True, "title": _("Create bulk email")})


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def bulk_email_preview(request):
    draft = request.session.get("bulk_email_draft")
    if not draft:
        messages.info(request, _("No draft found. Start a new bulk email."))
        return redirect("communications:bulk_email_create")

    # Look up template for design info
    template_obj = None
    design = DESIGN_BRANDED
    design_label = ""
    tid = draft.get("template_id")
    if tid:
        try:
            template_obj = EmailTemplate.objects.get(id=tid)
            design = template_obj.design
            design_label = template_obj.get_design_display()
        except EmailTemplate.DoesNotExist:
            pass
    footer_text = get_global_footer()
    rendered_html = _wrap_in_design(draft["body"], design, footer_text)

    recipients = resolve_recipients(
        filter_criteria=draft.get("filter_criteria"),
        manual_emails=draft.get("manual_emails", ""),
        search_query=draft.get("recipient_search", ""),
    )
    source_counts: dict[str, int] = {}
    for r in recipients:
        source_counts[r["source"]] = source_counts.get(r["source"], 0) + 1

    if request.method == "POST":
        if not recipients:
            messages.error(request, _("No recipients resolved."))
            return redirect("communications:bulk_email_create")

        if has_unresolved_variables(draft["subject"]) or has_unresolved_variables(draft["body"]):
            messages.error(
                request,
                _("Email contains unresolved template variables (e.g. {{ variable }}). Resolve them before sending."),
            )
            return render(request, "ui/pages/admin/communication_preview.html", {
                "draft": draft,
                "recipients": recipients,
                "recipient_count": len(recipients),
                "source_counts": source_counts,
                "rendered_html": rendered_html,
                "design_label": design_label,
            })

        bulk_send = create_bulk_draft(
            subject=draft["subject"],
            body=draft["body"],
            template_id=draft.get("template_id"),
            filter_criteria=draft.get("filter_criteria"),
            manual_emails=draft.get("manual_emails", ""),
            resolved_recipients=recipients,
            sent_by=request.user,
        )
        try:
            enqueue_bulk_email_send(bulk_send)
        except Exception:  # noqa: BLE001 - broker outage must not 500 the request; the draft is retained for retry
            logger.exception("Bulk email enqueue failed for bulk_send=%s", bulk_send.id)
            bulk_send.status = BULK_STATUS_FAILED
            bulk_send.save(update_fields=["status", "updated_at"])
            messages.error(
                request,
                _("The email could not be queued because the background worker is unavailable. "
                  "The draft was kept so you can retry sending."),
            )
            return redirect("communications:bulk_email_history")

        record_audit_event(
            actor=request.user,
            action=ACTION_BULK_EMAIL_SENT,
            target_type="bulk_email_send",
            target_id=str(bulk_send.id),
            metadata={
                "subject": draft["subject"],
                "recipient_count": len(recipients),
                "status": "queued",
            },
        )

        del request.session["bulk_email_draft"]
        messages.success(
            request,
            _("Bulk email queued for %(count)d recipients. Sending will continue in the background.")
            % {"count": len(recipients)},
        )
        return redirect("communications:bulk_email_history")

    return render(request, "ui/pages/admin/communication_preview.html", {
        "draft": draft,
        "recipients": recipients,
        "recipient_count": len(recipients),
        "source_counts": source_counts,
        "rendered_html": rendered_html,
        "design_label": design_label,
    })


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def bulk_email_history(request):
    sends = BulkEmailSend.objects.filter(sent_by=request.user).select_related("template")
    page_obj, pagination_qs = paginate(request, sends)
    return render(request, "ui/pages/admin/communication_history.html", {
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
    return render(request, "ui/pages/admin/communication_detail.html", {
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
    footer = EmailFooter.objects.first()
    return render(request, "ui/pages/admin/communication_template_list.html", {
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
        design = request.POST.get("design", "").strip()
        required_vars = request.POST.getlist("required_variables")
        if not subject:
            messages.error(request, _("Subject is required."))
        elif not body:
            messages.error(request, _("Body is required."))
        else:
            template.subject = subject
            template.body = body
            template.is_enabled_for_action = enabled
            template.required_variables = required_vars
            if design in dict(DESIGN_CHOICES):
                template.design = design
            try:
                template.full_clean()
                template.save(update_fields=["subject", "body", "is_enabled_for_action", "design", "required_variables"])
                messages.success(request, _("Template updated."))
                return redirect("communications:admin_template_list")
            except ValidationError as e:
                for field, errors in e.message_dict.items():
                    for err in errors:
                        messages.error(request, _("%(field)s: %(error)s") % {"field": field, "error": err})
    avail = AVAILABLE_VARIABLES_BY_SLUG.get(slug, {})
    all_vars = avail.get("optional", []) + avail.get("required", [])
    return render(request, "ui/pages/admin/communication_template_form.html", {
        "template": template,
        "available_vars": avail,
        "all_vars": all_vars,
        "required_vars": template.required_variables or [],
        "all_variable_samples": ALL_VARIABLE_SAMPLES,
        "all_slugs": TEMPLATE_SLUGS,
        "design_choices": DESIGN_CHOICES,
        "href_preview": reverse("communications:admin_template_preview", args=[slug]),
        "href_list": reverse("communications:admin_template_list"),
    })


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_template_preview(request, slug):
    """Preview an EmailTemplate rendered with sample context.

    The body is rendered through the model's engine-aware ``render()`` and
    then escaped at this preview boundary: editable staff HTML must not
    execute inside the admin page, even though the live email design keeps
    the engine-rendered body.
    """
    template = get_object_or_404(EmailTemplate, slug=slug)
    design = request.GET.get("design", template.design)
    if design not in dict(DESIGN_CHOICES):
        design = template.design
    context = SAMPLE_CONTEXT_BY_SLUG.get(slug, {})
    try:
        rendered_subject, rendered_body = template.render(context)
    except Exception:
        rendered_subject, rendered_body = template.subject, template.body
    footer_text = get_global_footer()
    rendered_html = render_to_string(
        f"communications/email_designs/{design}.html",
        {"content": escape(rendered_body), "footer": footer_text},
        request=request,
    )
    return render(request, "ui/pages/admin/communication_template_form.html", {
        "template": template,
        "rendered_subject": rendered_subject,
        "rendered_body": rendered_body,
        "rendered_html": rendered_html,
        "sample_context": context,
        "missing": [v for v in template.variable_names() if v not in context],
        "design_choices": DESIGN_CHOICES,
        "current_design": design,
    })


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_template_variables(request, slug: str):
    """HTMX partial: available variables for the given slug."""
    if not request.headers.get("HX-Request"):
        raise Http404
    avail = AVAILABLE_VARIABLES_BY_SLUG.get(slug, {})
    all_vars = avail.get("optional", []) + avail.get("required", [])
    return render(request, "ui/fragments/admin/_options.html", {
        "available_vars": avail,
        "all_vars": all_vars,
        "all_variable_samples": ALL_VARIABLE_SAMPLES,
        "editable": False,
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
        design = request.POST.get("design", "").strip()
        if design not in dict(DESIGN_CHOICES):
            design = DESIGN_BRANDED
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
                is_enabled_for_action=enabled, design=design,
            )
            try:
                template.full_clean()
                template.save()
                messages.success(request, _("Template created."))
                return redirect("communications:admin_template_list")
            except ValidationError as e:
                for field, errors in e.message_dict.items():
                    for err in errors:
                        messages.error(request, _("%(field)s: %(error)s") % {"field": field, "error": err})
    return render(request, "ui/pages/admin/communication_resend.html", {
        "available_slugs": available,
        "all_slugs": TEMPLATE_SLUGS,
        "design_choices": DESIGN_CHOICES,
        "href_list": reverse("communications:admin_template_list"),
    })


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_http_methods(["GET", "POST"])
def admin_footer_edit(request):
    """Edit the global email footer."""
    footer = EmailFooter.objects.first()
    if not footer:
        footer = EmailFooter.objects.create(body="")
    if request.method == "POST":
        body = request.POST.get("body", "").strip()
        footer.body = body
        footer.save(update_fields=["body"])
        messages.success(request, _("Global footer updated."))
        return redirect("communications:admin_template_list")
    return render(request, "ui/pages/admin/communication_footer.html", {
        "footer": footer,
    })
