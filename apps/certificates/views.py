"""Views for certificate management (admin and student)."""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
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
from apps.core.permissions import ROLE_SENIOR_STAFF, is_admin, is_moderator, role_required
from apps.progression.constants import CERTIFICATE_STATUS_AVAILABLE
from apps.students.models import StudentProfile

from .forms import CertificateTemplateForm
from .models import CertificateTemplate, GeneratedCertificate
from .services import generate_certificate_pdf, revoke_certificate

logger = logging.getLogger(__name__)

def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


# ---------------------------------------------------------------------------
# Admin: template management
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_template_list(request):
    templates = CertificateTemplate.objects.select_related("diploma", "created_by").all()
    ctx = {
        "templates": templates,
        "breadcrumb_items": [{"label": _("Certificate Templates")}],
    }
    return render(request, "certificates/admin_template_list.html", ctx)


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
    return render(request, "certificates/admin_template_form.html", ctx)


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
        "title": _("Edit certificate template"),
        "breadcrumb_items": [
            {"label": _("Certificate Templates"), "href": reverse("certificates:admin_template_list")},
            {"label": template.name},
        ],
    }
    return render(request, "certificates/admin_template_form.html", ctx)


# ---------------------------------------------------------------------------
# Admin: certificate management
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_certificate_list(request):
    certs = GeneratedCertificate.objects.select_related(
        "student__user", "diploma_enrollment__cohort__diploma", "certificate_template",
    ).all().order_by("-generated_at")

    ctx = {
        "certificates": certs,
        "breadcrumb_items": [{"label": _("Certificates")}],
    }
    return render(request, "certificates/admin_certificate_list.html", ctx)


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
            messages.warning(request, _("Certificate generation completed with status: %s") % cert.status)
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
    from .services import regenerate_certificate

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
            messages.warning(request, _("Regeneration completed with status: %s") % new_cert.status)
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("certificates:admin_certificate_list")


# ---------------------------------------------------------------------------
# Student: certificate access
# ---------------------------------------------------------------------------


@login_required
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
    return render(request, "certificates/student_certificate_list.html", ctx)


@login_required
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

    from apps.storage.services import storage_service
    try:
        url = storage_service.get_protected_download_url(
            cert.pdf_storage_key,
            user=request.user,
            permission_fn=lambda u: (
                getattr(u, "student_profile", None) == cert.student
                or is_admin(u) or is_moderator(u)
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
