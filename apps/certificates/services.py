"""Certificate services: template management, PDF generation, certificate lifecycle."""

from __future__ import annotations

import logging
from datetime import date
from io import BytesIO

from django.db import transaction
from django.template import Context, Template
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.academics.models import DiplomaEnrollment
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
from apps.storage.services import storage_service
from apps.students.models import StudentProfile

from .models import CertificateCounter, CertificateTemplate, GeneratedCertificate

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


def generate_certificate_pdf(
    student: StudentProfile,
    enrollment: DiplomaEnrollment,
    *,
    actor=None,
) -> GeneratedCertificate:
    completion = StudentDiplomaCompletion.objects.filter(
        diploma_enrollment=enrollment,
        status__in=[DIPLOMA_COMPLETION_COMPLETED, DIPLOMA_COMPLETION_GRADUATED],
    ).first()

    if not completion:
        raise ValueError(_("Student has not completed/graduated yet."))

    template = CertificateTemplate.objects.filter(
        diploma=enrollment.cohort.diploma,
        active=True,
    ).first()

    if not template:
        template = _create_placeholder_template(enrollment, actor)

    # Reserve the immutable record with its final number in a short transaction;
    # slow PDF render and storage work run outside it (T6). A previously failed
    # certificate is reused as-is so a retry never allocates a second number.
    with transaction.atomic():
        existing_active = GeneratedCertificate.objects.select_for_update().filter(
            diploma_enrollment=enrollment,
            status__in=[CERTIFICATE_STATUS_GENERATED, CERTIFICATE_STATUS_AVAILABLE],
        ).first()
        if existing_active:
            raise ValueError(_("An active certificate already exists for this enrollment."))

        cert = GeneratedCertificate.objects.select_for_update().filter(
            diploma_enrollment=enrollment,
            status=CERTIFICATE_STATUS_FAILED,
        ).first()
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
                },
            )

    try:
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

        snapshot = dict(cert.metadata_snapshot or {})
        snapshot.pop("error", None)
        cert.pdf_storage_key = result.key
        cert.pdf_file_size = result.byte_size
        cert.status = CERTIFICATE_STATUS_AVAILABLE
        cert.metadata_snapshot = snapshot
        cert.save(update_fields=["pdf_storage_key", "pdf_file_size", "status", "metadata_snapshot"])

    except (OSError, ValueError) as exc:
        logger.exception("PDF generation failed for cert %s", cert.certificate_number)
        snapshot = dict(cert.metadata_snapshot or {})
        snapshot["error"] = str(exc)
        cert.status = CERTIFICATE_STATUS_FAILED
        cert.metadata_snapshot = snapshot
        cert.save(update_fields=["status", "metadata_snapshot"])

    # ponytail: broad Exception replaced with OSError/ValueError; any other error propagates to caller

    return cert


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


def revoke_certificate(
    cert: GeneratedCertificate,
    *,
    actor=None,
    reason: str = "",
) -> GeneratedCertificate:
    if not reason:
        raise ValueError(_("Reason is required for revocation."))
    cert.status = CERTIFICATE_STATUS_REVOKED
    cert.revoked_at = timezone.now()
    cert.revoked_by = actor
    cert.revoke_reason = reason
    cert.save(
        update_fields=["status", "revoked_at", "revoked_by", "revoke_reason"],
    )
    return cert


def regenerate_certificate(
    cert: GeneratedCertificate,
    *,
    actor=None,
    reason: str = "",
) -> GeneratedCertificate:
    if not reason:
        raise ValueError(_("Reason is required for regeneration."))
    cert.status = CERTIFICATE_STATUS_REGENERATION
    cert.save(update_fields=["status"])

    PromotionDecision.objects.create(
        student=cert.student,
        diploma_enrollment=cert.diploma_enrollment,
        decision_type=PROMOTION_DECISION_GRADUATE,
        automatic=False,
        reason=reason,
        decided_by=actor,
    )

    return generate_certificate_pdf(
        cert.student, cert.diploma_enrollment, actor=actor,
    )
