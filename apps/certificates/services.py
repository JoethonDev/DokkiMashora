"""Certificate services: template management, PDF generation, certificate lifecycle."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from io import BytesIO

from django.db import transaction
from django.template import Context, Template
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.academics.models import Diploma, DiplomaEnrollment
from apps.progression.constants import (
    CERTIFICATE_STATUS_AVAILABLE,
    CERTIFICATE_STATUS_FAILED,
    CERTIFICATE_STATUS_GENERATED,
    CERTIFICATE_STATUS_REGENERATION,
    CERTIFICATE_STATUS_REVOKED,
    DIPLOMA_COMPLETION_COMPLETED,
    DIPLOMA_COMPLETION_GRADUATED,
    PROMOTION_DECISION_GRADUATE,
)
from apps.progression.models import PromotionDecision, StudentDiplomaCompletion
from apps.storage.services import record_deferred_storage_deletion, storage_service
from apps.students.models import StudentProfile

from .models import (
    FIELD_KEY_DATE,
    FIELD_KEY_STUDENT_NAME,
    MAPPING_SCHEMA_VERSION,
    TEMPLATE_SOURCE_FORMAT_NONE,
    TEMPLATE_SOURCE_STATUS_NONE,
    TEMPLATE_SOURCE_STATUS_READY,
    CertificateCounter,
    CertificateTemplate,
    GeneratedCertificate,
)
from .source_adapters import (
    render_source_preview,
    source_checksum,
    validate_field_mapping,
    validate_source_format,
    validate_source_size,
)

try:
    from weasyprint import HTML
except (ImportError, OSError):
    HTML = None

try:
    from xhtml2pdf import pisa
except ImportError:
    pisa = None

logger = logging.getLogger(__name__)


def get_next_certificate_number(diploma_id: int, student_id: int) -> str:
    year = date.today().year
    with transaction.atomic():
        counter, _created = CertificateCounter.objects.get_or_create(
            diploma_id=diploma_id,
            year=year,
        )
        counter = CertificateCounter.objects.select_for_update().get(id=counter.id)
        counter.last_sequence += 1
        counter.save(update_fields=["last_sequence"])
        seq = counter.last_sequence

    return f"DM-{year}-{diploma_id}-{student_id}-{seq:04d}"


def render_certificate_html(
    student: StudentProfile,
    template: CertificateTemplate,
    completion: StudentDiplomaCompletion,
    certificate_number: str,
) -> str:
    user = student.user
    context = {
        "student_name": user.full_name_ar or user.full_name or user.email,
        "diploma_title": completion.diploma_enrollment.cohort.diploma.title,
        "cohort_name": completion.diploma_enrollment.cohort.name,
        "completion_date": completion.completed_at.strftime("%Y-%m-%d") if completion.completed_at else "",
        "certificate_number": certificate_number,
        "issue_date": timezone.now().strftime("%Y-%m-%d"),
        "student": student,
        "user": user,
        "completion": completion,
        "enrollment": completion.diploma_enrollment,
    }

    if template.body_html:
        t = Template(template.body_html)
        return t.render(Context(context))

    return render_to_string("certificates/default_certificate.html", context)


def sample_field_values(*, today=None) -> dict[str, str]:
    """Sample values for preview rendering (never persisted, never issued)."""
    today = today or date.today()
    return {
        FIELD_KEY_STUDENT_NAME: "Student Name",
        FIELD_KEY_DATE: today.strftime("%Y-%m-%d"),
    }


def resolve_certificate_field_values(
    student: StudentProfile,
    enrollment: DiplomaEnrollment,
    completion: StudentDiplomaCompletion,
    certificate_number: str,
    *,
    today=None,
) -> dict[str, str]:
    """Resolve values for the registered dynamic fields (P7-03 v2).

    Only fields with a resolver today (student_name, date) are returned;
    reserved placeholder fields (certificate_number, diploma, course, semester,
    cohort, graduation_date) are intentionally absent so the renderer skips
    them until their resolvers are wired. ``student_name`` keeps the current
    corrected-name contract: full_name_ar, else full_name, else email.
    """
    user = student.user
    return {
        FIELD_KEY_STUDENT_NAME: user.full_name_ar or user.full_name or user.email,
        FIELD_KEY_DATE: (today or date.today()).strftime("%Y-%m-%d"),
    }


def generate_certificate_pdf(
    student: StudentProfile,
    enrollment: DiplomaEnrollment,
    *,
    actor=None,
    regeneration_cert_id: int | None = None,
) -> GeneratedCertificate:
    completion = StudentDiplomaCompletion.objects.filter(
        diploma_enrollment=enrollment,
        status__in=[DIPLOMA_COMPLETION_COMPLETED, DIPLOMA_COMPLETION_GRADUATED],
    ).first()

    if not completion:
        raise ValueError(_("Student has not completed/graduated yet."))

    # Reserve the immutable record with its final number in a short transaction;
    # slow PDF render and storage work run outside it (T6). A previously failed
    # certificate is reused as-is so a retry never allocates a second number.
    with transaction.atomic():
        locked_enrollment = DiplomaEnrollment.objects.select_for_update().get(pk=enrollment.pk)
        Diploma.objects.select_for_update().get(pk=locked_enrollment.cohort.diploma_id)
        template = CertificateTemplate.objects.filter(
            diploma=locked_enrollment.cohort.diploma,
            active=True,
        ).first()
        if not template:
            template = _create_placeholder_template(locked_enrollment, actor)
        existing_active = GeneratedCertificate.objects.select_for_update().filter(
            diploma_enrollment=enrollment,
            status__in=[
                CERTIFICATE_STATUS_GENERATED,
                CERTIFICATE_STATUS_AVAILABLE,
                CERTIFICATE_STATUS_REGENERATION,
            ],
        ).first()
        if existing_active:
            stale_before = timezone.now() - timedelta(minutes=15)
            if existing_active.status == CERTIFICATE_STATUS_AVAILABLE:
                raise ValueError(_("An active certificate already exists for this enrollment."))
            if (
                existing_active.status == CERTIFICATE_STATUS_GENERATED
                and existing_active.updated_at > stale_before
            ):
                raise ValueError(_("Certificate generation is already in progress."))
            if (
                existing_active.status == CERTIFICATE_STATUS_REGENERATION
                and existing_active.pk != regeneration_cert_id
                and existing_active.updated_at > stale_before
            ):
                raise ValueError(_("Certificate regeneration is already in progress."))

        cert = existing_active or GeneratedCertificate.objects.select_for_update().filter(
            diploma_enrollment=locked_enrollment, status=CERTIFICATE_STATUS_FAILED,
        ).order_by("-generated_at").first()
        if cert is None:
            certificate_number = get_next_certificate_number(
                enrollment.cohort.diploma_id, student.id,
            )
            cert = GeneratedCertificate.objects.create(
                student=student,
                diploma_enrollment=enrollment,
                certificate_template=template,
                certificate_number=certificate_number,
                status=CERTIFICATE_STATUS_GENERATED,
                generated_by=actor,
                metadata_snapshot={
                    "student_name": student.user.full_name,
                    "diploma": enrollment.cohort.diploma.title,
                    "cohort": enrollment.cohort.name,
                    "completed_at": str(completion.completed_at) if completion.completed_at else None,
                    "mapping_schema_version": template.mapping_schema_version,
                },
            )
        else:
            cert.status = CERTIFICATE_STATUS_GENERATED
            cert.certificate_template = template
            cert.generated_by = actor
            cert.save(update_fields=["status", "certificate_template", "generated_by", "updated_at"])

    uploaded_key = ""
    try:
        # P7-03 v2: resolve values for all registered dynamic fields at
        # generation time; student_name keeps the current corrected name
        # (full_name_ar, else full_name, else email).
        field_values = resolve_certificate_field_values(
            student, enrollment, completion, cert.certificate_number,
        )
        student_name = field_values.get(FIELD_KEY_STUDENT_NAME, "")

        if template.source_status == TEMPLATE_SOURCE_STATUS_READY and template.source_storage_key and template.field_mapping:
            source_bytes = storage_service.download_file(template.source_storage_key)
            from .source_adapters import overlay_text_on_source
            output_bytes = overlay_text_on_source(
                source_bytes,
                template.source_format,
                template.field_mapping,
                field_values,
            )
            pdf_bytes = output_bytes if template.source_format == "pdf" else _image_bytes_to_pdf(output_bytes)
        else:
            html = render_certificate_html(student, template, completion, cert.certificate_number)
            pdf_bytes = _html_to_pdf(html)

        result = storage_service.upload_file(
            data=pdf_bytes,
            key_prefix=f"certificates/{enrollment.id}",
            content_type="application/pdf",
            max_size=10 * 1024 * 1024,
            allowed_types=[".pdf"],
            original_filename=f"certificate_{cert.certificate_number}.pdf",
        )
        uploaded_key = result.key

        snapshot = dict(cert.metadata_snapshot or {})
        snapshot.pop("error", None)
        snapshot["student_name"] = student_name
        snapshot["mapping_schema_version"] = template.mapping_schema_version
        cert.pdf_storage_key = result.key
        cert.pdf_file_size = result.byte_size
        cert.status = CERTIFICATE_STATUS_AVAILABLE
        cert.metadata_snapshot = snapshot
        cert.save(update_fields=["pdf_storage_key", "pdf_file_size", "status", "metadata_snapshot"])

    except Exception as exc:  # rendering and storage failures are retryable domain failures
        logger.exception("PDF generation failed for cert %s", cert.certificate_number)
        snapshot = dict(cert.metadata_snapshot or {})
        snapshot["error"] = str(exc)
        cert.status = CERTIFICATE_STATUS_FAILED
        cert.metadata_snapshot = snapshot
        cert.save(update_fields=["status", "metadata_snapshot", "updated_at"])
        if uploaded_key:
            try:
                if not storage_service.delete_file(uploaded_key):
                    record_deferred_storage_deletion(key=uploaded_key, error=str(exc))
            except Exception as cleanup_exc:
                record_deferred_storage_deletion(key=uploaded_key, error=str(cleanup_exc))

    return cert


def _image_bytes_to_pdf(png_bytes: bytes) -> bytes:
    """Wrap a PNG (raster overlay output) into a single-page PDF via PIL."""
    from PIL import Image
    from reportlab.pdfgen import canvas as pdf_canvas

    img = Image.open(BytesIO(png_bytes))
    buf = BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=(img.width, img.height))
    c.drawInlineImage(img, 0, 0, width=img.width, height=img.height)
    c.showPage()
    c.save()
    return buf.getvalue()


def _html_to_pdf(html: str) -> bytes:
    if HTML is not None:
        pdf_bytes = BytesIO()
        HTML(string=html).write_pdf(pdf_bytes)
        return pdf_bytes.getvalue()
    if pisa is not None:
        pdf_bytes = BytesIO()
        pisa.CreatePDF(html, dest=pdf_bytes)
        return pdf_bytes.getvalue()
    raise ImportError("Neither weasyprint nor xhtml2pdf are available.")


def _create_placeholder_template(enrollment: DiplomaEnrollment, actor) -> CertificateTemplate:
    body_html = """
    <div style="text-align: center; font-family: 'Traditional Arabic', 'Amiri', 'Noto Naskh Arabic', 'Scheherazade New', serif; padding: 40px; direction: rtl;">
        <h1>{{ certificate_number }}</h1>
        <h2>{{ diploma_title }}</h2>
        <p>يشهد هذا المستند بأن</p>
        <h3>{{ student_name }}</h3>
        <p>قد أتم بنجاح متطلبات</p>
        <h3>{{ cohort_name }}</h3>
        <p>التاريخ: {{ completion_date }}</p>
        <p>تاريخ الإصدار: {{ issue_date }}</p>
        <p style="margin-top: 30px; font-size: 12px; color: #667582;">هذه شهادة مؤقتة لحين إعداد النموذج الرسمي.</p>
    </div>
    """
    template, _created = CertificateTemplate.objects.update_or_create(
        diploma=enrollment.cohort.diploma,
        language="ar",
        template_type="completion",
        defaults={
            "name": f"Placeholder - {enrollment.cohort.diploma.title}",
            "active": True,
            "is_placeholder": True,
            "body_html": body_html,
            "created_by": actor or enrollment.student.user,
        },
    )
    return template


def ingest_certificate_source(
    template: CertificateTemplate,
    *,
    data: bytes,
    filename: str,
    content_type: str,
    actor=None,
    field_mapping: dict | None = None,
) -> CertificateTemplate:
    """Validate, store, preview, and map a PDF/JPG certificate source (P7-02).

    File validation and preview rendering run OUTSIDE DB transactions; the
    template row is updated in a short transaction only after all slow work
    succeeds, so invalid/oversized/malformed sources never leave authoritative
    DB rows or untracked objects.
    """
    validate_source_size(data)
    source_format = validate_source_format(filename, content_type)
    preview = render_source_preview(data, source_format)
    checksum = source_checksum(data)

    if field_mapping:
        normalized_mapping = validate_field_mapping(field_mapping, preview)
    else:
        normalized_mapping = {}

    old_source_key = template.source_storage_key
    old_preview_key = template.preview_storage_key
    source_result = storage_service.upload_file(
        data=data,
        key_prefix=f"certificate_templates/{template.id}",
        content_type=content_type,
        max_size=25 * 1024 * 1024,
        allowed_types=[".pdf", ".jpg", ".jpeg"],
        original_filename=filename,
    )
    try:
        preview_result = storage_service.upload_file(
            data=preview.preview_bytes,
            key_prefix=f"certificate_templates/{template.id}/preview",
            content_type="image/png",
            max_size=8 * 1024 * 1024,
            allowed_types=[".png"],
            original_filename="preview.png",
        )
    except Exception:
        try:
            if not storage_service.delete_file(source_result.key):
                record_deferred_storage_deletion(key=source_result.key, error="Preview upload failed")
        except Exception as cleanup_exc:
            record_deferred_storage_deletion(key=source_result.key, error=str(cleanup_exc))
        raise

    template.source_format = source_format
    template.source_status = TEMPLATE_SOURCE_STATUS_READY
    template.source_storage_key = source_result.key
    template.source_byte_size = source_result.byte_size
    template.source_checksum = checksum
    template.source_error = ""
    template.preview_storage_key = preview_result.key
    template.preview_width = preview.width
    template.preview_height = preview.height
    template.preview_page_count = preview.page_count
    template.field_mapping = normalized_mapping
    template.mapping_schema_version = MAPPING_SCHEMA_VERSION
    try:
        template.save(update_fields=[
            "source_format", "source_status", "source_storage_key", "source_byte_size",
            "source_checksum", "source_error", "preview_storage_key", "preview_width",
            "preview_height", "preview_page_count", "field_mapping",
            "mapping_schema_version", "updated_at",
        ])
    except Exception:
        for key in (source_result.key, preview_result.key):
            try:
                if not storage_service.delete_file(key):
                    record_deferred_storage_deletion(key=key, error="Template database update failed")
            except Exception as cleanup_exc:
                record_deferred_storage_deletion(key=key, error=str(cleanup_exc))
        raise
    for key in (old_source_key, old_preview_key):
        if key and key not in (source_result.key, preview_result.key):
            try:
                if not storage_service.delete_file(key):
                    record_deferred_storage_deletion(key=key, error="Template source replaced")
            except Exception as exc:
                record_deferred_storage_deletion(key=key, error=str(exc))
    return template


def clear_certificate_source(template: CertificateTemplate) -> CertificateTemplate:
    """Remove a template's source/preview mapping (Admin only)."""
    old_source_key = template.source_storage_key
    old_preview_key = template.preview_storage_key
    template.source_format = TEMPLATE_SOURCE_FORMAT_NONE
    template.source_status = TEMPLATE_SOURCE_STATUS_NONE
    template.source_storage_key = ""
    template.source_byte_size = None
    template.source_checksum = ""
    template.source_error = ""
    template.preview_storage_key = ""
    template.preview_width = None
    template.preview_height = None
    template.preview_page_count = 1
    template.field_mapping = {}
    template.mapping_schema_version = 0
    template.save(update_fields=[
        "source_format", "source_status", "source_storage_key", "source_byte_size",
        "source_checksum", "source_error", "preview_storage_key", "preview_width",
        "preview_height", "preview_page_count", "field_mapping",
        "mapping_schema_version", "updated_at",
    ])
    for key in (old_source_key, old_preview_key):
        if key:
            try:
                if not storage_service.delete_file(key):
                    record_deferred_storage_deletion(key=key, error="Certificate source cleared")
            except Exception as exc:
                logger.exception("Failed to clean certificate template object %s", key)
                record_deferred_storage_deletion(key=key, error=str(exc))
    return template


def revoke_certificate(
    cert: GeneratedCertificate,
    *,
    actor=None,
    reason: str = "",
) -> GeneratedCertificate:
    if not reason:
        raise ValueError(_("Reason is required for revocation."))
    with transaction.atomic():
        locked = GeneratedCertificate.objects.select_for_update().get(pk=cert.pk)
        if locked.status != CERTIFICATE_STATUS_AVAILABLE:
            raise ValueError(_("Only an available certificate can be revoked."))
        locked.status = CERTIFICATE_STATUS_REVOKED
        locked.revoked_at = timezone.now()
        locked.revoked_by = actor
        locked.revoke_reason = reason
        locked.save(
            update_fields=[
                "status", "revoked_at", "revoked_by", "revoke_reason", "updated_at",
            ],
        )
    return locked


def regenerate_certificate(
    cert: GeneratedCertificate,
    *,
    actor=None,
    reason: str = "",
) -> GeneratedCertificate:
    if not reason:
        raise ValueError(_("Reason is required for regeneration."))
    if not cert.diploma_enrollment_id:
        raise ValueError(_("This historical certificate is no longer linked to an enrollment."))
    with transaction.atomic():
        locked_enrollment = DiplomaEnrollment.objects.select_for_update().get(
            pk=cert.diploma_enrollment_id,
        )
        locked = GeneratedCertificate.objects.select_for_update().select_related(
            "student", "diploma_enrollment",
        ).get(pk=cert.pk)
        if locked.status != CERTIFICATE_STATUS_AVAILABLE:
            raise ValueError(_("Only an available certificate can be regenerated."))
        if locked.diploma_enrollment_id != locked_enrollment.pk:
            raise ValueError(_("Certificate enrollment changed; refresh and try again."))
        locked.status = CERTIFICATE_STATUS_REGENERATION
        locked.save(update_fields=["status", "updated_at"])

        PromotionDecision.objects.create(
            student=locked.student,
            diploma_enrollment=locked.diploma_enrollment,
            decision_type=PROMOTION_DECISION_GRADUATE,
            automatic=False,
            reason=reason,
            decided_by=actor,
        )

    return generate_certificate_pdf(
        locked.student,
        locked.diploma_enrollment,
        actor=actor,
        regeneration_cert_id=locked.pk,
    )
