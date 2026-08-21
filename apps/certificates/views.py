"""Views for certificate management (admin and student)."""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.academics.models import DiplomaEnrollment
from apps.audit.constants import (
    ACTION_CERTIFICATE_GENERATED,
    ACTION_CERTIFICATE_REGENERATED,
    ACTION_CERTIFICATE_REVOKED,
)
from apps.audit.services import record_audit_event
from apps.core.pagination import paginate
from apps.core.permissions import ROLE_SENIOR_STAFF, is_admin, role_required, student_required
from apps.progression.constants import CERTIFICATE_STATUS_AVAILABLE
from apps.storage.services import storage_service
from apps.students.models import StudentProfile

from .forms import CertificateTemplateForm
from .models import CertificateTemplate, GeneratedCertificate
from .services import (
    _image_bytes_to_pdf,
    clear_certificate_source,
    generate_certificate_pdf,
    ingest_certificate_source,
    regenerate_certificate,
    revoke_certificate,
)
from .source_adapters import SourcePreview, overlay_text_on_source, validate_field_mapping

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Admin: template management
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_template_list(request):
    templates = CertificateTemplate.objects.select_related("diploma", "created_by").order_by("-created_at")
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
        form = CertificateTemplateForm(request.POST, request.FILES)
        if form.is_valid():
            template = form.save(commit=False)
            template.created_by = request.user
            template.save()
            messages.success(request, _("Template created."))
            return redirect("certificates:admin_template_list")
    else:
        form = CertificateTemplateForm()

    ctx = {
        "form": form,
        "title": _("Create certificate template"),
        "breadcrumb_items": [
            {"label": _("Certificate Templates"), "href": reverse("certificates:admin_template_list")},
            {"label": _("New template")},
        ],
    }
    return render(request, "ui/pages/admin/certificates.html", {**ctx, "title": _("Certificate template")})


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_template_edit(request, template_id):
    template = get_object_or_404(CertificateTemplate, id=template_id)
    if request.method == "POST":
        form = CertificateTemplateForm(request.POST, request.FILES, instance=template)
        if form.is_valid():
            form.save()
            messages.success(request, _("Template updated."))
            return redirect("certificates:admin_template_list")
    else:
        form = CertificateTemplateForm(instance=template)

    ctx = {
        "form": form,
        "template": template,
        "title": _("Edit certificate template"),
        "breadcrumb_items": [
            {"label": _("Certificate Templates"), "href": reverse("certificates:admin_template_list")},
            {"label": template.name},
        ],
    }
    return render(request, "ui/pages/admin/certificates.html", {**ctx, "title": _("Certificate template")})


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_template_preview(request, template_id):
    """Protected preview of a stored PDF/PSD template source (P7-02)."""
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
@require_POST
def admin_template_upload_source(request, template_id):
    """Admin-only validated PDF/PSD source upload (P7-02)."""
    template = get_object_or_404(CertificateTemplate, id=template_id)
    file = request.FILES.get("source_file")
    if file is None:
        messages.error(request, _("Select a PDF or PSD source file."))
        return redirect("certificates:admin_template_edit", template_id=template.id)
    try:
        data = file.read()
        ingest_certificate_source(
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
    messages.success(request, _("Template source uploaded and previewed."))
    return redirect("certificates:admin_template_edit", template_id=template.id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_template_clear_source(request, template_id):
    """Admin-only removal of a template source (P7-02)."""
    template = get_object_or_404(CertificateTemplate, id=template_id)
    clear_certificate_source(template)
    messages.success(request, _("Template source removed."))
    return redirect("certificates:admin_template_edit", template_id=template.id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_template_editor(request, template_id):
    """Visual placement editor (P7-03): load preview, place Student Name, save mapping."""
    template = get_object_or_404(CertificateTemplate, id=template_id)
    if template.source_status != "ready" or not template.preview_storage_key:
        messages.error(request, _("Upload and preview a PDF/PSD source before editing placement."))
        return redirect("certificates:admin_template_edit", template_id=template.id)
    preview_url = storage_service.get_protected_download_url(
        template.preview_storage_key,
        user=request.user,
        permission_fn=lambda u: is_admin(u) or u.is_superuser,
    )
    ctx = {
        "template": template,
        "preview_url": preview_url,
        "preview_width": template.preview_width,
        "preview_height": template.preview_height,
        "page_count": template.preview_page_count,
        "current_mapping": template.field_mapping,
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
    import json

    template = get_object_or_404(CertificateTemplate, id=template_id)
    if template.source_status != "ready":
        return JsonResponse({"error": _("No source preview available.")}, status=400)
    try:
        raw = json.loads(request.body or "{}")
        mapping = raw.get("mapping") or {}
    except (json.JSONDecodeError, ValueError):
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
    template.mapping_schema_version = 1
    template.save(update_fields=["field_mapping", "mapping_schema_version", "updated_at"])
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
    import json

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
    except json.JSONDecodeError:
        return JsonResponse({"error": _("Invalid mapping JSON.")}, status=400)
    submitted = raw.get("mapping") or {}
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
        {"student_name": "Student Name"},
    )
    pdf_bytes = output_bytes if template.source_format == "pdf" else _image_bytes_to_pdf(output_bytes)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = 'inline; filename="certificate_preview.pdf"'
    response["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------------------------
# Admin: certificate management
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_certificate_list(request):
    student_id = request.GET.get("student_id", "")
    certs = GeneratedCertificate.objects.select_related(
        "student__user", "diploma_enrollment__cohort__diploma", "certificate_template",
    ).all().order_by("-generated_at")
    if student_id.isdigit():
        certs = certs.filter(student_id=int(student_id))

    page_obj, pagination_qs = paginate(request, certs)
    ctx = {
        "page_obj": page_obj,
        "pagination_qs": pagination_qs,
        "breadcrumb_items": [{"label": _("Certificates")}],
        "student_id": student_id,
    }
    return render(request, "ui/pages/admin/certificates.html", {**ctx, "title": _("Certificates")})


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_generate_certificate(request, student_id):
    student = get_object_or_404(StudentProfile, id=student_id)
    enrollment_id = request.POST.get("enrollment_id")
    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id, student=student)

    try:
        cert = generate_certificate_pdf(student, enrollment, actor=request.user)
        record_audit_event(
            actor=request.user,
            action=ACTION_CERTIFICATE_GENERATED,
            target_type="certificate",
            target_id=str(cert.id),
            metadata={
                "student_id": student.id,
                "enrollment_id": enrollment_id,
                "status": cert.status,
            },
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

    return redirect("certificates:admin_certificate_list")


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_revoke_certificate(request, cert_id):
    cert = get_object_or_404(GeneratedCertificate, id=cert_id)
    reason = request.POST.get("reason", "")

    try:
        revoke_certificate(cert, actor=request.user, reason=reason)
        record_audit_event(
            actor=request.user,
            action=ACTION_CERTIFICATE_REVOKED,
            target_type="certificate",
            target_id=str(cert_id),
            metadata={
                "student_id": cert.student_id,
                "certificate_number": cert.certificate_number,
                "reason": reason,
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
        record_audit_event(
            actor=request.user,
            action=ACTION_CERTIFICATE_REGENERATED,
            target_type="certificate",
            target_id=str(new_cert.id),
            metadata={
                "previous_cert_id": cert_id,
                "student_id": new_cert.student_id,
                "reason": reason,
                "status": new_cert.status,
            },
        )
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


@student_required
def student_certificate_list(request):
    student_profile = getattr(request.user, "student_profile", None)
    if not student_profile:
        messages.error(request, _("Student profile not found."))
        return redirect("core:home")

    certs = GeneratedCertificate.objects.filter(
        student=student_profile,
        status=CERTIFICATE_STATUS_AVAILABLE,
    ).select_related("certificate_template", "diploma_enrollment__cohort__diploma").order_by("-generated_at")

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
        status=CERTIFICATE_STATUS_AVAILABLE,
    )
    return _serve_certificate_pdf(request, cert)
