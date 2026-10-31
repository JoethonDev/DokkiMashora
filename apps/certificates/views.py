"""Views for certificate management (admin and student)."""

import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.academics.constants import ENROLLMENT_STATUS_COMPLETED
from apps.academics.models import Course, CourseEnrollment, Diploma, DiplomaEnrollment
from apps.audit.constants import (
    ACTION_CERTIFICATE_REVOKED,
    ACTION_CERTIFICATE_TEMPLATE_CREATED,
    ACTION_CERTIFICATE_TEMPLATE_UPDATED,
    SECTION_CERTIFICATES,
)
from apps.audit.services import record_audit_event
from apps.core.pagination import paginate
from apps.core.permissions import (
    ROLE_SENIOR_STAFF,
    is_admin,
    is_student,
    role_required,
    safe_int,
    student_required,
)
from apps.progression.constants import (
    CERTIFICATE_STATUS_AVAILABLE,
    CERTIFICATE_STATUS_GENERATED,
    CERTIFICATE_STATUS_REGENERATION,
    CERTIFICATE_STATUS_SUPERSEDED,
    DIPLOMA_COMPLETION_COMPLETED,
    DIPLOMA_COMPLETION_GRADUATED,
)
from apps.progression.models import StudentDiplomaCompletion
from apps.storage.services import storage_service
from apps.students.models import StudentProfile

from .forms import CertificateTemplateDetailsForm, CertificateTemplateForm
from .models import (
    FIELD_KEY_LABELS,
    MAPPING_FIELD_REGISTRY,
    MAPPING_SCHEMA_VERSION,
    CertificateTemplate,
    GeneratedCertificate,
)
from .services import (
    _image_bytes_to_pdf,
    clear_certificate_source,
    delete_certificate_template,
    ingest_certificate_source,
    issue_certificate,
    issue_course_certificate,
    load_certificate_logo_data_uri,
    logo_data_uri_from_bytes,
    preview_field_values,
    regenerate_certificate,
    render_certificate_design_html,
    render_template_preview,
    revoke_certificate,
    sample_field_values,
)
from .source_adapters import (
    FONT_REGISTRY,
    SourcePreview,
    overlay_text_on_source,
    validate_field_mapping,
)

logger = logging.getLogger(__name__)

_TEMPLATE_AUDIT_FIELDS = (
    "name",
    "diploma_id",
    "course_id",
    "language",
    "template_type",
    "active",
    "is_placeholder",
    "source_format",
    "source_status",
    "mapping_schema_version",
)


def _template_audit_label(template) -> str:
    program_title = template.program_title
    return f"{template.name} · {program_title}" if program_title else template.name


def _template_audit_snapshot(template) -> dict:
    return {field: getattr(template, field) for field in _TEMPLATE_AUDIT_FIELDS}


def _changed_template_fields(before: dict, after: dict) -> list[str]:
    return [field for field in before if before[field] != after[field]]


def _template_form_context(request, form, template=None) -> dict:
    source_ready = bool(template and template.source_status == "ready")
    return {
        "form": form,
        "template": template,
        "is_edit": bool(template and template.pk),
        "source_ready": source_ready,
        "source_required": not source_ready,
        "pdf_preview_url": (
            reverse("certificates:admin_template_generated_preview", args=[template.id])
            if source_ready and template.field_mapping
            else ""
        ),
        "title": _("Certificate template"),
        "breadcrumb_items": [
            {"label": _("Certificate Templates"), "href": reverse("certificates:admin_template_list")},
            {"label": template.name if template else _("New template")},
        ],
        "back_url": reverse("certificates:admin_template_list"),
        "back_label": _("Back to templates"),
    }


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_template_list(request):
    templates = CertificateTemplate.objects.select_related(
        "diploma",
        "course",
        "created_by",
    ).order_by("-created_at")
    page_obj, pagination_qs = paginate(request, templates)
    ctx = {
        "page_obj": page_obj,
        "templates": page_obj,
        "pagination_qs": pagination_qs,
        "breadcrumb_items": [{"label": _("Certificate Templates")}],
    }
    return render(request, "ui/pages/admin/certificates.html", {**ctx, "title": _("Certificate templates")})


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_template_create(request):
    if request.method == "POST":
        form = CertificateTemplateDetailsForm(request.POST, request.FILES)
        if form.is_valid():
            source_bytes, source_name, source_content_type = form.get_source_payload()
            if source_bytes is None:
                form.add_error("source_file", _("Upload certificate artwork to continue."))
            else:
                template = form.save(commit=False)
                template.created_by = request.user
                template.save()
                try:
                    ingest_certificate_source(
                        template,
                        data=source_bytes,
                        filename=source_name,
                        content_type=source_content_type,
                        actor=request.user,
                    )
                except ValueError as exc:
                    messages.error(request, str(exc))
                    return redirect("certificates:admin_template_edit", template_id=template.id)
                except Exception:
                    logger.exception("Certificate source upload failed for new template %s", template.id)
                    messages.error(
                        request,
                        _("The artwork could not be processed. Check the format and upload it again."),
                    )
                    return redirect("certificates:admin_template_edit", template_id=template.id)
                record_audit_event(
                    actor=request.user,
                    action=ACTION_CERTIFICATE_TEMPLATE_CREATED,
                    target_type="certificate_template",
                    target_id=str(template.id),
                    section=SECTION_CERTIFICATES,
                    target_label=_template_audit_label(template),
                    metadata={
                        **_template_audit_snapshot(template),
                        "source_filename": source_name,
                        "source_content_type": source_content_type,
                    },
                )
                messages.success(request, _("Template created and artwork uploaded."))
                return redirect("certificates:admin_template_editor", template_id=template.id)
    else:
        form = CertificateTemplateDetailsForm()

    ctx = _template_form_context(request, form)
    return render(request, "ui/pages/admin/certificate_template_form.html", ctx)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_template_edit(request, template_id):
    template = get_object_or_404(CertificateTemplate, id=template_id)
    source_ready = template.source_status == "ready"
    form = None
    if source_ready:
        if request.method == "POST":
            before = _template_audit_snapshot(template)
            form = CertificateTemplateDetailsForm(request.POST, request.FILES, instance=template)
            if form.is_valid():
                template = form.save()
                after = _template_audit_snapshot(template)
                record_audit_event(
                    actor=request.user,
                    action=ACTION_CERTIFICATE_TEMPLATE_UPDATED,
                    target_type="certificate_template",
                    target_id=str(template.id),
                    section=SECTION_CERTIFICATES,
                    target_label=_template_audit_label(template),
                    metadata={
                        "before": before,
                        "after": after,
                        "changed_fields": _changed_template_fields(before, after),
                    },
                )
                messages.success(request, _("Template updated."))
                return redirect("certificates:admin_template_edit", template_id=template.id)
        else:
            form = CertificateTemplateDetailsForm(instance=template)
    elif request.method == "POST":
        # Artwork is mandatory; the page only exposes the artwork upload form
        # until a source is ready.
        return redirect("certificates:admin_template_edit", template_id=template.id)

    ctx = _template_form_context(request, form, template)
    return render(request, "ui/pages/admin/certificate_template_form.html", ctx)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_template_design_preview(request):
    """Render the no-code design preview without saving any template state."""
    template = None
    raw_id = (request.POST.get("template_id") or "").strip()
    if raw_id.isdigit():
        template = CertificateTemplate.objects.filter(pk=int(raw_id)).first()
    form = CertificateTemplateForm(request.POST, request.FILES, instance=template)
    if not form.is_valid():
        return JsonResponse(
            {
                "error": _("Fix the highlighted fields to refresh the preview."),
                "errors": form.errors.get_json_data(),
            },
            status=422,
        )

    preview_template = form.save(commit=False)
    preview_template.design = form.build_design()
    logo_bytes, _content_type = form.get_logo_payload()
    if logo_bytes:
        logo_data_uri = logo_data_uri_from_bytes(logo_bytes)
    elif form.cleaned_data.get("remove_logo") and template:
        logo_data_uri = ""
    else:
        logo_data_uri = (
            load_certificate_logo_data_uri(preview_template)
            if preview_template.pk
            else ""
        )
    html = render_certificate_design_html(
        preview_template,
        preview_field_values(preview_template),
        for_pdf=False,
        logo_data_uri=logo_data_uri,
    )
    response = JsonResponse({"html": html})
    response["Cache-Control"] = "no-store"
    return response


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_template_delete(request, template_id):
    """Delete an inactive certificate template and its stored artwork."""
    template = get_object_or_404(CertificateTemplate, id=template_id)
    try:
        delete_certificate_template(template=template, actor=request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("certificates:admin_template_edit", template_id=template.id)
    messages.success(request, _("Certificate template deleted."))
    return redirect("certificates:admin_template_list")


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_template_preview(request, template_id):
    """Protected preview of a stored PDF/JPG/legacy-PSD template source (P7-02)."""
    template = get_object_or_404(CertificateTemplate, id=template_id)
    if not template.preview_storage_key:
        messages.error(request, _("This template has no source preview."))
        return redirect("certificates:admin_template_list")
    url = storage_service.get_protected_download_url(
        template.preview_storage_key,
        user=request.user,
        permission_fn=lambda u: is_admin(u) or u.is_superuser,
    )
    return redirect(url)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_template_generated_preview(request, template_id):
    """Render a sample PDF from the persisted template without issuing anything."""
    template = get_object_or_404(CertificateTemplate, id=template_id)
    try:
        pdf_bytes = render_template_preview(template)
    except Exception:
        logger.exception(
            "Certificate template preview failed for template %s",
            template.id,
        )
        messages.error(request, _("The certificate preview could not be generated."))
        return redirect("certificates:admin_template_edit", template_id=template.id)

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = 'inline; filename="certificate_preview.pdf"'
    response["Cache-Control"] = "no-store"
    return response


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_template_upload_source(request, template_id):
    """Admin-only validated PDF/PSD source upload (P7-02)."""
    template = get_object_or_404(CertificateTemplate, id=template_id)
    file = request.FILES.get("source_file")
    if file is None:
        messages.error(request, _("Select a PDF, PSD, or JPG/JPEG source file."))
        return redirect("certificates:admin_template_edit", template_id=template.id)
    try:
        data = file.read()
        before = _template_audit_snapshot(template)
        template = ingest_certificate_source(
            template,
            data=data,
            filename=file.name,
            content_type=file.content_type or "",
            actor=request.user,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("certificates:admin_template_edit", template_id=template.id)
    except Exception:
        logger.exception("Certificate source upload failed for template %s", template.id)
        messages.error(request, _("The source could not be processed. Check the format and try again."))
        return redirect("certificates:admin_template_edit", template_id=template.id)
    after = _template_audit_snapshot(template)
    record_audit_event(
        actor=request.user,
        action=ACTION_CERTIFICATE_TEMPLATE_UPDATED,
        target_type="certificate_template",
        target_id=str(template.id),
        section=SECTION_CERTIFICATES,
        target_label=_template_audit_label(template),
        metadata={
            "operation": "source_uploaded",
            "source_filename": file.name,
            "source_content_type": file.content_type or "",
            "before": before,
            "after": after,
            "changed_fields": _changed_template_fields(before, after),
        },
    )
    messages.success(request, _("Template source uploaded and previewed."))
    return redirect("certificates:admin_template_edit", template_id=template.id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_template_clear_source(request, template_id):
    """Admin-only removal of a template source (P7-02)."""
    template = get_object_or_404(CertificateTemplate, id=template_id)
    before = _template_audit_snapshot(template)
    template = clear_certificate_source(template)
    after = _template_audit_snapshot(template)
    record_audit_event(
        actor=request.user,
        action=ACTION_CERTIFICATE_TEMPLATE_UPDATED,
        target_type="certificate_template",
        target_id=str(template.id),
        section=SECTION_CERTIFICATES,
        target_label=_template_audit_label(template),
        metadata={
            "operation": "source_cleared",
            "before": before,
            "after": after,
            "changed_fields": _changed_template_fields(before, after),
        },
    )
    messages.success(request, _("Template source removed."))
    return redirect("certificates:admin_template_edit", template_id=template.id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_template_editor(request, template_id):
    """Visual placement editor (P7-03): load preview, place fields, save mapping."""
    template = get_object_or_404(CertificateTemplate, id=template_id)
    if template.source_status != "ready" or not template.preview_storage_key:
        messages.error(request, _("Upload and preview a PDF/JPG source before editing placement."))
        return redirect("certificates:admin_template_edit", template_id=template.id)
    preview_url = storage_service.get_protected_download_url(
        template.preview_storage_key,
        user=request.user,
        permission_fn=lambda u: is_admin(u) or u.is_superuser,
    )
    available_fields = [
        {"key": key, "label": str(FIELD_KEY_LABELS.get(key, key))}
        for key in MAPPING_FIELD_REGISTRY
    ]
    available_fonts = [
        {"key": key, "label": label}
        for key, (_filename, label) in FONT_REGISTRY.items()
    ]
    ctx = {
        "template": template,
        "preview_url": preview_url,
        "preview_width": template.preview_width,
        "preview_height": template.preview_height,
        "page_count": template.preview_page_count,
        "current_mapping": template.field_mapping,
        "mapping_schema_version": template.mapping_schema_version or MAPPING_SCHEMA_VERSION,
        "available_fields": available_fields,
        "available_fonts": available_fonts,
        "mapping_save_url": reverse("certificates:admin_template_save_mapping", args=[template.id]),
        "preview_generate_url": reverse("certificates:admin_template_preview_generate", args=[template.id]),
        "breadcrumb_items": [
            {"label": _("Certificate Templates"), "href": reverse("certificates:admin_template_list")},
            {"label": template.name},
            {"label": _("Placement editor")},
        ],
        "title": _("Placement editor"),
        "back_url": reverse("certificates:admin_template_edit", args=[template.id]),
        "back_label": _("Back to template"),
    }
    return render(request, "ui/pages/admin/certificate_editor.html", ctx)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_template_save_mapping(request, template_id):
    """Validate and persist the placement mapping (P7-03)."""
    template = get_object_or_404(CertificateTemplate, id=template_id)
    if template.source_status != "ready":
        return JsonResponse({"error": _("No source preview available.")}, status=400)
    try:
        raw = json.loads(request.body or "{}")
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": _("Invalid mapping JSON.")}, status=400)
    if not isinstance(raw, dict):
        return JsonResponse({"error": _("Invalid mapping JSON.")}, status=400)
    mapping = raw.get("mapping") or {}
    if not isinstance(mapping, dict):
        return JsonResponse({"error": _("Invalid mapping JSON.")}, status=400)

    preview = SourcePreview(
        preview_bytes=b"",
        width=template.preview_width or 0,
        height=template.preview_height or 0,
        page_count=template.preview_page_count or 1,
    )
    try:
        normalized = validate_field_mapping(mapping, preview)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=422)

    template.field_mapping = normalized
    template.mapping_schema_version = MAPPING_SCHEMA_VERSION
    template.save(update_fields=["field_mapping", "mapping_schema_version", "updated_at"])
    record_audit_event(
        actor=request.user,
        action=ACTION_CERTIFICATE_TEMPLATE_UPDATED,
        target_type="certificate_template",
        target_id=str(template.id),
        section=SECTION_CERTIFICATES,
        target_label=_template_audit_label(template),
        metadata={
            "operation": "mapping_saved",
            "changed_fields": ["field_mapping"],
            "mapping_field_count": len(normalized),
            "mapping_schema_version": MAPPING_SCHEMA_VERSION,
        },
    )
    return JsonResponse({"ok": True, "mapping": normalized})


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_template_preview_generate(request, template_id):
    """Render a generated preview with sample/current authorized data (P7-03).

    Uses a stable sample name — never issues or mutates certificate history.
    Accepts the CURRENT in-editor mapping (server-validated) so the preview
    reflects what the admin sees, not silently stale persisted state. When no
    mapping is sent, falls back to the persisted mapping.
    """
    template = get_object_or_404(CertificateTemplate, id=template_id)
    if template.source_status != "ready" or not template.source_storage_key:
        return JsonResponse({"error": _("No source available.")}, status=400)

    preview = SourcePreview(
        preview_bytes=b"",
        width=template.preview_width or 0,
        height=template.preview_height or 0,
        page_count=template.preview_page_count or 1,
    )

    mapping = template.field_mapping
    try:
        raw = json.loads(request.body or "{}")
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": _("Invalid mapping JSON.")}, status=400)
    if not isinstance(raw, dict):
        return JsonResponse({"error": _("Invalid mapping JSON.")}, status=400)
    submitted = raw.get("mapping") or {}
    if not isinstance(submitted, dict):
        return JsonResponse({"error": _("Invalid mapping JSON.")}, status=400)
    if submitted:
        try:
            mapping = validate_field_mapping(submitted, preview)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=422)

    if not mapping:
        return JsonResponse({"error": _("Save a placement mapping first.")}, status=422)

    source_bytes = storage_service.download_file(template.source_storage_key)
    output_bytes = overlay_text_on_source(
        source_bytes,
        template.source_format,
        mapping,
        sample_field_values(),
    )
    pdf_bytes = output_bytes if template.source_format == "pdf" else _image_bytes_to_pdf(output_bytes)
    record_audit_event(
        actor=request.user,
        action=ACTION_CERTIFICATE_TEMPLATE_UPDATED,
        target_type="certificate_template",
        target_id=str(template.id),
        section=SECTION_CERTIFICATES,
        target_label=_template_audit_label(template),
        metadata={
            "operation": "preview_generated",
            "mapping_source": "submitted" if submitted else "persisted",
            "mapping_field_count": len(mapping),
        },
    )
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = 'inline; filename="certificate_preview.pdf"'
    response["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------------------------
# Admin: certificate management
# ---------------------------------------------------------------------------


def _certificate_student_search_q(query: str) -> Q:
    """Search a certificate/completion queryset by the owning student."""
    return (
        Q(student__full_name__icontains=query)
        | Q(student__user__full_name__icontains=query)
        | Q(student__user__full_name_ar__icontains=query)
        | Q(student__user__email__icontains=query)
        | Q(student__phone__icontains=query)
    )


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_certificate_list(request):
    student_id = request.GET.get("student_id", "")
    search_query = (request.GET.get("q", "") or "").strip()[:100]
    program_value = (request.GET.get("program", "") or "").strip()

    program_options = [
        {
            "value": f"diploma:{diploma.id}",
            "label": f"{_('Diploma')}: {diploma.title}",
        }
        for diploma in Diploma.objects.order_by("title")
    ]
    program_options.extend(
        {
            "value": f"course:{course.id}",
            "label": f"{_('Course')}: {course.title}",
        }
        for course in Course.objects.order_by("title")
    )
    valid_program_values = {option["value"] for option in program_options}
    if program_value not in valid_program_values:
        program_value = ""
    for option in program_options:
        option["selected"] = option["value"] == program_value

    program_kind, _separator, raw_program_id = program_value.partition(":")
    program_id = safe_int(raw_program_id) if program_value else None

    certs = GeneratedCertificate.objects.select_related(
        "student__user",
        "diploma_enrollment__cohort__diploma",
        "course_enrollment__course_version__course",
        "certificate_template",
    ).all().order_by("-generated_at")
    if student_id.isdigit():
        certs = certs.filter(student_id=int(student_id))
    if search_query:
        certs = certs.filter(_certificate_student_search_q(search_query))
    if program_kind == "diploma":
        certs = certs.filter(diploma_enrollment__cohort__diploma_id=program_id)
    elif program_kind == "course":
        certs = certs.filter(course_enrollment__course_version__course_id=program_id)
    student_filter_active = student_id.isdigit()
    has_active_filters = bool(search_query or program_value or student_filter_active)

    # Manual fallback: completed/graduated students can be issued a certificate
    # here even when automatic issuance was skipped or failed. Search/program
    # filters run in the database before the bounded recent-results window.
    diploma_completions = StudentDiplomaCompletion.objects.filter(
        status__in=(DIPLOMA_COMPLETION_COMPLETED, DIPLOMA_COMPLETION_GRADUATED),
    ).select_related(
        "student__user", "diploma_enrollment__cohort__diploma",
    ).annotate(
        effective_completed_at=Coalesce("completed_at", "graduated_at"),
    ).order_by("-effective_completed_at", "-id")
    course_enrollments = CourseEnrollment.objects.filter(
        status=ENROLLMENT_STATUS_COMPLETED,
    ).select_related(
        "student__user", "course_version__course",
    ).annotate(
        effective_completed_at=Coalesce("completed_at", "updated_at"),
    ).order_by("-effective_completed_at", "-id")

    if student_filter_active:
        diploma_completions = diploma_completions.filter(student_id=int(student_id))
        course_enrollments = course_enrollments.filter(student_id=int(student_id))
    if search_query:
        student_search = _certificate_student_search_q(search_query)
        diploma_completions = diploma_completions.filter(student_search)
        course_enrollments = course_enrollments.filter(student_search)
    if program_kind == "diploma":
        diploma_completions = diploma_completions.filter(
            diploma_enrollment__cohort__diploma_id=program_id,
        )
        course_enrollments = course_enrollments.none()
    elif program_kind == "course":
        diploma_completions = diploma_completions.none()
        course_enrollments = course_enrollments.filter(
            course_version__course_id=program_id,
        )

    manual_total = diploma_completions.count() + course_enrollments.count()
    manual_items = []
    for completion in diploma_completions[:50]:
        manual_items.append({
            "scope": "diploma",
            "student": completion.student,
            "student_id": completion.student_id,
            "enrollment_id": completion.diploma_enrollment_id,
            "program_title": completion.diploma_enrollment.cohort.diploma.title,
            "program_detail": completion.diploma_enrollment.cohort.name,
            "completed_at": completion.completed_at or completion.graduated_at,
            "completion_status": completion.get_status_display(),
            "active_certificate": None,
        })
    for enrollment in course_enrollments[:50]:
        manual_items.append({
            "scope": "course",
            "student": enrollment.student,
            "student_id": enrollment.student_id,
            "enrollment_id": enrollment.id,
            "program_title": enrollment.course_version.course.title,
            "program_detail": enrollment.course_version.label,
            "completed_at": enrollment.completed_at or enrollment.updated_at,
            "completion_status": enrollment.get_status_display(),
            "active_certificate": None,
        })

    manual_items.sort(
        key=lambda item: item["completed_at"] or timezone.now(),
        reverse=True,
    )
    manual_items = manual_items[:50]
    manual_truncated = manual_total > len(manual_items)

    if manual_items:
        diploma_ids = [
            item["enrollment_id"] for item in manual_items if item["scope"] == "diploma"
        ]
        course_ids = [
            item["enrollment_id"] for item in manual_items if item["scope"] == "course"
        ]
        active_by_scope = {}
        for cert in GeneratedCertificate.objects.filter(
            Q(diploma_enrollment_id__in=diploma_ids)
            | Q(course_enrollment_id__in=course_ids),
            status__in=(
                CERTIFICATE_STATUS_GENERATED,
                CERTIFICATE_STATUS_AVAILABLE,
                CERTIFICATE_STATUS_REGENERATION,
            ),
        ):
            if cert.diploma_enrollment_id:
                active_by_scope[("diploma", cert.diploma_enrollment_id)] = cert
            elif cert.course_enrollment_id:
                active_by_scope[("course", cert.course_enrollment_id)] = cert
        for item in manual_items:
            item["active_certificate"] = active_by_scope.get(
                (item["scope"], item["enrollment_id"]),
            )

    page_obj, pagination_qs = paginate(request, certs)
    ctx = {
        "page_obj": page_obj,
        "pagination_qs": pagination_qs,
        "manual_items": manual_items,
        "manual_total": manual_total,
        "manual_truncated": manual_truncated,
        "show_manual_issuance": True,
        "show_certificate_filters": True,
        "search_value": search_query,
        "program_value": program_value,
        "program_options": program_options,
        "has_active_filters": has_active_filters,
        "student_filter_active": student_filter_active,
        "breadcrumb_items": [{"label": _("Certificates")}],
        "student_id": student_id,
    }
    return render(request, "ui/pages/admin/certificates.html", {**ctx, "title": _("Certificates")})


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_generate_certificate(request, student_id):
    student = get_object_or_404(StudentProfile, id=student_id)
    enrollment_id = safe_int(request.POST.get("enrollment_id"))
    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id, student=student)

    try:
        cert = issue_certificate(
            student,
            enrollment,
            actor=request.user,
            source="manual",
        )
        if cert.status == CERTIFICATE_STATUS_AVAILABLE:
            messages.success(request, _("Certificate generated."))
        else:
            messages.warning(
                request,
                _("Certificate generation completed with status: %(status)s")
                % {"status": cert.get_status_display()},
            )
    except ValueError as exc:
        messages.error(request, str(exc))

    next_url = request.POST.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()},
    ):
        return redirect(next_url)
    return redirect("certificates:admin_certificate_list")


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_generate_course_certificate(request, enrollment_id):
    enrollment = get_object_or_404(
        CourseEnrollment.objects.select_related(
            "student__user",
            "course_version__course",
        ),
        id=enrollment_id,
    )

    try:
        cert = issue_course_certificate(
            enrollment.student,
            enrollment,
            actor=request.user,
            source="manual",
        )
        if cert.status == CERTIFICATE_STATUS_AVAILABLE:
            messages.success(request, _("Course certificate generated."))
        else:
            messages.warning(
                request,
                _("Certificate generation completed with status: %(status)s")
                % {"status": cert.get_status_display()},
            )
    except ValueError as exc:
        messages.error(request, str(exc))

    next_url = request.POST.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()},
    ):
        return redirect(next_url)
    return redirect("certificates:admin_certificate_list")


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_revoke_certificate(request, cert_id):
    cert = get_object_or_404(GeneratedCertificate.objects.select_related("student"), id=cert_id)
    reason = request.POST.get("reason", "")

    try:
        cert = revoke_certificate(cert, actor=request.user, reason=reason)
        snapshot = cert.metadata_snapshot or {}
        record_audit_event(
            actor=request.user,
            action=ACTION_CERTIFICATE_REVOKED,
            target_type="certificate",
            target_id=str(cert_id),
            section=SECTION_CERTIFICATES,
            target_label=f"{cert.certificate_number} · {cert.student.full_name}",
            subject_type="student",
            subject_id=str(cert.student_id),
            subject_label=cert.student.full_name,
            metadata={
                "certificate_number": cert.certificate_number,
                "student_id": cert.student_id,
                "program_title": (
                    snapshot.get("program_title")
                    or snapshot.get("diploma")
                    or snapshot.get("course")
                    or ""
                ),
                "reason": reason,
                "status_before": CERTIFICATE_STATUS_AVAILABLE,
                "status_after": cert.status,
            },
        )
        messages.success(request, _("Certificate revoked."))
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("certificates:admin_certificate_list")


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_regenerate_certificate(request, cert_id):
    cert = get_object_or_404(GeneratedCertificate, id=cert_id)
    reason = request.POST.get("reason", "")

    try:
        new_cert = regenerate_certificate(cert, actor=request.user, reason=reason)
        if new_cert.status == CERTIFICATE_STATUS_AVAILABLE:
            messages.success(request, _("Certificate regenerated."))
        else:
            messages.warning(
                request,
                _("Regeneration completed with status: %(status)s")
                % {"status": new_cert.get_status_display()},
            )
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("certificates:admin_certificate_list")


# ---------------------------------------------------------------------------
# Student: certificate access
# ---------------------------------------------------------------------------


@login_required
def student_certificate_list(request):
    # The unqualified certificates URL is the student portal entry point, but
    # administrators arriving there should land in the all-student manager.
    if is_admin(request.user):
        return redirect("certificates:admin_certificate_list")
    if not (is_student(request.user) or request.user.is_superuser):
        raise PermissionDenied

    student_profile = getattr(request.user, "student_profile", None)
    if not student_profile:
        messages.error(request, _("Student profile not found."))
        return redirect("core:home")

    certs = GeneratedCertificate.objects.filter(
        student=student_profile,
        status=CERTIFICATE_STATUS_AVAILABLE,
    ).select_related(
        "certificate_template",
        "diploma_enrollment__cohort__diploma",
        "course_enrollment__course_version__course",
    ).order_by("-generated_at")

    ctx = {
        "certificates": certs,
    }
    return render(request, "ui/pages/student/certificates.html", {**ctx, "title": _("Certificates")})


@student_required
def student_certificate_download(request, cert_id):
    student_profile = getattr(request.user, "student_profile", None)
    cert = get_object_or_404(
        GeneratedCertificate,
        id=cert_id,
        student=student_profile,
        status=CERTIFICATE_STATUS_AVAILABLE,
    )
    return _serve_certificate_pdf(request, cert)


def _serve_certificate_pdf(request, cert):
    if not cert.pdf_storage_key:
        messages.error(request, _("Certificate PDF is not available."))
        return redirect("certificates:student_certificate_list")

    try:
        url = storage_service.get_protected_download_url(
            cert.pdf_storage_key,
            user=request.user,
            permission_fn=lambda u: (
                getattr(u, "student_profile", None) == cert.student
                or is_admin(u)
            ),
        )
        return redirect(url)
    except Exception:
        logger.exception("Failed to serve certificate PDF %s", cert.id)
        messages.error(request, _("Failed to load certificate PDF."))
        return redirect("certificates:student_certificate_list")


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_download_certificate(request, cert_id):
    cert = get_object_or_404(
        GeneratedCertificate,
        id=cert_id,
        status__in=(CERTIFICATE_STATUS_AVAILABLE, CERTIFICATE_STATUS_SUPERSEDED),
    )
    return _serve_certificate_pdf(request, cert)
