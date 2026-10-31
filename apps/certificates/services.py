"""Certificate services: template management, PDF generation, certificate lifecycle."""

from __future__ import annotations

import base64
import logging
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.template import Context, Template
from django.template.loader import render_to_string
from django.utils import timezone, translation
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from PIL import Image
from reportlab.pdfgen import canvas as pdf_canvas

from apps.academics.constants import ENROLLMENT_STATUS_COMPLETED
from apps.academics.models import Course, CourseEnrollment, Diploma, DiplomaEnrollment
from apps.audit.constants import (
    ACTION_CERTIFICATE_GENERATED,
    ACTION_CERTIFICATE_REGENERATED,
    ACTION_CERTIFICATE_TEMPLATE_DELETED,
    SECTION_CERTIFICATES,
)
from apps.audit.services import record_audit_event
from apps.progression.constants import (
    CERTIFICATE_STATUS_AVAILABLE,
    CERTIFICATE_STATUS_FAILED,
    CERTIFICATE_STATUS_GENERATED,
    CERTIFICATE_STATUS_REGENERATION,
    CERTIFICATE_STATUS_REVOKED,
    CERTIFICATE_STATUS_SUPERSEDED,
    DIPLOMA_COMPLETION_COMPLETED,
    DIPLOMA_COMPLETION_GRADUATED,
)
from apps.progression.models import StudentDiplomaCompletion
from apps.storage.services import record_deferred_storage_deletion, storage_service
from apps.students.models import StudentProfile

from .design import (
    MAX_LOGO_BYTES,
    design_ink_colors,
    design_text_defaults,
    normalize_design,
    normalize_language,
    validate_logo_bytes,
)
from .models import (
    FIELD_KEY_CERTIFICATE_NUMBER,
    FIELD_KEY_COHORT,
    FIELD_KEY_COURSE,
    FIELD_KEY_COURSE_VERSION,
    FIELD_KEY_DATE,
    FIELD_KEY_DIPLOMA,
    FIELD_KEY_GRADUATION_DATE,
    FIELD_KEY_SEMESTER,
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
    overlay_text_on_source,
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


def _next_certificate_sequence(*, diploma_id=None, course_id=None) -> tuple[int, int]:
    year = date.today().year
    scope_filter = (
        {"diploma_id": diploma_id}
        if diploma_id is not None
        else {"course_id": course_id}
    )
    with transaction.atomic():
        counter, _created = CertificateCounter.objects.get_or_create(
            year=year,
            **scope_filter,
        )
        counter = CertificateCounter.objects.select_for_update().get(id=counter.id)
        counter.last_sequence += 1
        counter.save(update_fields=["last_sequence"])
        return year, counter.last_sequence


def get_next_certificate_number(diploma_id: int, student_id: int) -> str:
    year, seq = _next_certificate_sequence(diploma_id=diploma_id)
    return f"DM-{year}-{diploma_id}-{student_id}-{seq:04d}"


def get_next_course_certificate_number(course_id: int, student_id: int) -> str:
    year, seq = _next_certificate_sequence(course_id=course_id)
    return f"CR-{year}-{course_id}-{student_id}-{seq:04d}"


def _logo_data_uri_from_bytes(data: bytes) -> str:
    """Build a bounded data URI for a validated PNG/JPG logo."""
    if not data:
        return ""
    try:
        image = Image.open(BytesIO(data))
        image_format = image.format
    except Exception:
        logger.warning("Stored certificate logo could not be opened")
        return ""
    mime_type = {"PNG": "image/png", "JPEG": "image/jpeg"}.get(image_format)
    if mime_type is None:
        logger.warning("Stored certificate logo has unsupported format %s", image_format)
        return ""
    return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"


def logo_data_uri_from_bytes(data: bytes) -> str:
    """Public wrapper for an already validated logo upload."""
    return _logo_data_uri_from_bytes(data)


def load_certificate_logo_data_uri(template: CertificateTemplate) -> str:
    if not template.logo_storage_key:
        return ""
    try:
        data = storage_service.download_file(template.logo_storage_key)
    except Exception:
        logger.warning(
            "Certificate logo could not be loaded for template %s",
            template.id,
            exc_info=True,
        )
        return ""
    return _logo_data_uri_from_bytes(data)


def preview_field_values(template: CertificateTemplate) -> dict[str, str]:
    """Sample field values with the template's program names filled in."""
    values = sample_field_values()
    if template.diploma_id:
        title = template.diploma.title
        values[FIELD_KEY_DIPLOMA] = title
        values[FIELD_KEY_COURSE] = title
    elif template.course_id:
        values[FIELD_KEY_DIPLOMA] = ""
        values[FIELD_KEY_COURSE] = template.course.title
        with translation.override(normalize_language(template.language)):
            values[FIELD_KEY_COURSE_VERSION] = gettext("Version 1")
    return values


def _certificate_design_context(
    template: CertificateTemplate,
    field_values: dict[str, str],
    *,
    for_pdf: bool,
    logo_data_uri: str | None,
) -> dict:
    design = normalize_design(template.design or {})
    language = normalize_language(template.language)
    text_defaults = design_text_defaults(language)
    site_name = settings.SITE_NAME_AR if language == "ar" else settings.SITE_NAME
    ink_color, muted_color, heading_color = design_ink_colors(
        design["background_color"], design["accent_color"],
    )

    with translation.override(language):
        meta_items = []
        if design["show_certificate_number"]:
            value = field_values.get(FIELD_KEY_CERTIFICATE_NUMBER, "")
            if value:
                meta_items.append({"label": gettext("Certificate number"), "value": value})
        if design["show_completion_date"]:
            value = field_values.get(FIELD_KEY_GRADUATION_DATE, "")
            if value:
                meta_items.append({"label": gettext("Completion date:"), "value": value})
        if design["show_issue_date"]:
            value = field_values.get(FIELD_KEY_DATE, "")
            if value:
                meta_items.append({"label": gettext("Issue date:"), "value": value})
        program_title = (
            field_values.get(FIELD_KEY_COURSE)
            or field_values.get(FIELD_KEY_DIPLOMA)
            or ""
        )
        program_detail = (
            field_values.get(FIELD_KEY_COHORT)
            or field_values.get(FIELD_KEY_COURSE_VERSION)
            or ""
        )

    if logo_data_uri is None:
        logo_data_uri = load_certificate_logo_data_uri(template)

    fonts_dir = Path(__file__).resolve().parent / "fonts"
    return {
        "for_pdf": for_pdf,
        "font_regular_path": (fonts_dir / "Cairo-400.ttf").as_posix(),
        "font_bold_path": (fonts_dir / "Cairo-700.ttf").as_posix(),
        "font_stack": (
            "Cairo, sans-serif"
            if for_pdf
            else "Cairo, Tajawal, Amiri, 'Segoe UI', sans-serif"
        ),
        "html_lang": "en" if for_pdf else language,
        "is_rtl": language == "ar",
        "layout": design["layout"],
        "text_size": design["text_size"],
        "text_align": design["text_align"],
        "accent_color": design["accent_color"],
        "background_color": design["background_color"],
        "border_style": design["border_style"],
        "border_width": {"none": "0", "thin": "1.5pt", "bold": "5pt"}[design["border_style"]],
        "ink_color": ink_color,
        "muted_color": muted_color,
        "heading_color": heading_color,
        "show_rule": design["layout"] != "minimal",
        "show_band": design["layout"] == "modern",
        "site_name": site_name,
        "title": design["title_text"] or text_defaults["title_text"],
        "intro": design["intro_text"] or text_defaults["intro_text"],
        "closing": design["closing_text"] or text_defaults["closing_text"],
        "student_name": field_values.get(FIELD_KEY_STUDENT_NAME, ""),
        "program_title": program_title,
        "program_detail": program_detail,
        "show_program": design["show_program"] and bool(program_title),
        "show_cohort": design["show_cohort"] and bool(program_detail),
        "meta_items": meta_items,
        "show_meta": bool(meta_items),
        "show_signature": design["show_signature"],
        "signature_name": design["signature_name"],
        "signature_title": design["signature_title"],
        "footer_note": design["footer_note"],
        "logo_data_uri": logo_data_uri or "",
        "logo_size": design["logo_size"],
        "logo_height": {"small": "12mm", "medium": "18mm", "large": "24mm"}[design["logo_size"]],
    }


def render_certificate_design_html(
    template: CertificateTemplate,
    field_values: dict[str, str],
    *,
    for_pdf: bool = False,
    logo_data_uri: str | None = None,
) -> str:
    context = _certificate_design_context(
        template,
        field_values,
        for_pdf=for_pdf,
        logo_data_uri=logo_data_uri,
    )
    return render_to_string("certificates/designed_certificate.html", context)


def _legacy_html_certificate(template: CertificateTemplate, context: dict) -> str:
    return Template(template.body_html).render(Context(context))


def render_certificate_html(
    student: StudentProfile,
    template: CertificateTemplate,
    completion: StudentDiplomaCompletion,
    certificate_number: str,
) -> str:
    user = student.user
    field_values = resolve_certificate_field_values(
        student,
        completion.diploma_enrollment,
        completion,
        certificate_number,
    )
    # A visual design always wins over the legacy raw HTML body. Templates with
    # neither use the same visual defaults, so no HTML/CSS knowledge is needed.
    if template.body_html and not template.design:
        completion_date = _certificate_completion_date(completion)
        context = {
            "student_name": user.full_name_ar or user.full_name or user.email,
            "diploma_title": completion.diploma_enrollment.cohort.diploma.title,
            "course_title": "",
            "course_version": "",
            "cohort_name": completion.diploma_enrollment.cohort.name,
            "completion_date": completion_date.strftime("%Y-%m-%d") if completion_date else "",
            "certificate_number": certificate_number,
            "issue_date": timezone.now().strftime("%Y-%m-%d"),
            "student": student,
            "user": user,
            "completion": completion,
            "enrollment": completion.diploma_enrollment,
        }
        return _legacy_html_certificate(template, context)

    return render_certificate_design_html(template, field_values, for_pdf=True)


def render_course_certificate_html(
    student: StudentProfile,
    template: CertificateTemplate,
    enrollment: CourseEnrollment,
    certificate_number: str,
) -> str:
    user = student.user
    course = enrollment.course_version.course
    field_values = resolve_course_certificate_field_values(
        student,
        enrollment,
        certificate_number,
    )
    if template.body_html and not template.design:
        completion_date = enrollment.completed_at or enrollment.updated_at
        context = {
            "student_name": user.full_name_ar or user.full_name or user.email,
            "diploma_title": "",
            "course_title": course.title,
            "course_version": enrollment.course_version.label,
            "cohort_name": "",
            "completion_date": completion_date.strftime("%Y-%m-%d") if completion_date else "",
            "certificate_number": certificate_number,
            "issue_date": timezone.now().strftime("%Y-%m-%d"),
            "student": student,
            "user": user,
            "enrollment": enrollment,
            "course": course,
            "course_version_obj": enrollment.course_version,
        }
        return _legacy_html_certificate(template, context)

    return render_certificate_design_html(template, field_values, for_pdf=True)


def sample_field_values(*, today=None) -> dict[str, str]:
    """Sample values for preview rendering (never persisted, never issued)."""
    today = today or date.today()
    return {
        FIELD_KEY_STUDENT_NAME: "اسم الطالب",
        FIELD_KEY_DATE: today.strftime("%Y-%m-%d"),
        FIELD_KEY_CERTIFICATE_NUMBER: "DM-2026-0001",
        FIELD_KEY_DIPLOMA: "اسم الدبلومة",
        FIELD_KEY_COURSE: "اسم الدورة",
        FIELD_KEY_COURSE_VERSION: "الإصدار الأول",
        FIELD_KEY_SEMESTER: "الفصل الأول",
        FIELD_KEY_COHORT: "الدفعة 2026",
        FIELD_KEY_GRADUATION_DATE: today.strftime("%Y-%m-%d"),
    }


def render_certificate_preview_html(
    template: CertificateTemplate,
    field_values: dict[str, str],
) -> str:
    """Render an HTML certificate preview with sample values (never persisted)."""
    if template.design:
        return render_certificate_design_html(template, field_values, for_pdf=False)
    if template.body_html:
        context = {
            "student_name": field_values.get(FIELD_KEY_STUDENT_NAME, ""),
            "diploma_title": field_values.get(FIELD_KEY_DIPLOMA, ""),
            "course_title": field_values.get(FIELD_KEY_COURSE, ""),
            "course_version": field_values.get(FIELD_KEY_COURSE_VERSION, ""),
            "cohort_name": field_values.get(FIELD_KEY_COHORT, ""),
            "completion_date": field_values.get(FIELD_KEY_GRADUATION_DATE, ""),
            "certificate_number": field_values.get(FIELD_KEY_CERTIFICATE_NUMBER, ""),
            "issue_date": field_values.get(FIELD_KEY_DATE, ""),
        }
        return Template(template.body_html).render(Context(context))
    return render_certificate_design_html(template, field_values, for_pdf=False)


def render_template_preview(template: CertificateTemplate) -> bytes:
    """Generate a sample PDF for a template's persisted source or HTML design.

    Uses the same source overlay path and sample fields as the editor preview.
    This is a read-only preview and never creates or mutates certificate rows.
    """
    field_values = preview_field_values(template)

    if (
        template.source_status == TEMPLATE_SOURCE_STATUS_READY
        and template.source_storage_key
        and template.field_mapping
    ):
        source_bytes = storage_service.download_file(template.source_storage_key)
        output_bytes = overlay_text_on_source(
            source_bytes,
            template.source_format,
            template.field_mapping,
            field_values,
        )
        return (
            output_bytes
            if template.source_format == "pdf"
            else _image_bytes_to_pdf(output_bytes)
        )

    return _html_to_pdf(render_certificate_preview_html(template, field_values))


def resolve_certificate_field_values(
    student: StudentProfile,
    enrollment: DiplomaEnrollment,
    completion: StudentDiplomaCompletion,
    certificate_number: str,
    *,
    today=None,
) -> dict[str, str]:
    """Resolve every registered dynamic field for a generated certificate.

    ``course`` is a compatibility field for templates shared with course
    certificates; diploma completion has no single course relation, so the
    diploma title is the stable program value for that field.
    """
    user = student.user
    diploma = enrollment.cohort.diploma
    completion_date = completion.completed_at or completion.graduated_at
    latest_progress = (
        enrollment.semester_progress
        .select_related("semester")
        .order_by("-semester__order", "-updated_at")
        .first()
    )
    return {
        FIELD_KEY_STUDENT_NAME: user.full_name_ar or user.full_name or user.email,
        FIELD_KEY_DATE: (today or date.today()).strftime("%Y-%m-%d"),
        FIELD_KEY_CERTIFICATE_NUMBER: certificate_number,
        FIELD_KEY_DIPLOMA: diploma.title,
        FIELD_KEY_COURSE: diploma.title,
        FIELD_KEY_COURSE_VERSION: "",
        FIELD_KEY_SEMESTER: latest_progress.semester.title if latest_progress else "",
        FIELD_KEY_COHORT: enrollment.cohort.name,
        FIELD_KEY_GRADUATION_DATE: completion_date.strftime("%Y-%m-%d") if completion_date else "",
    }


def resolve_course_certificate_field_values(
    student: StudentProfile,
    enrollment: CourseEnrollment,
    certificate_number: str,
    *,
    today=None,
) -> dict[str, str]:
    """Resolve every registered dynamic field for a course certificate."""
    user = student.user
    course = enrollment.course_version.course
    completion_date = enrollment.completed_at or enrollment.updated_at
    return {
        FIELD_KEY_STUDENT_NAME: user.full_name_ar or user.full_name or user.email,
        FIELD_KEY_DATE: (today or date.today()).strftime("%Y-%m-%d"),
        FIELD_KEY_CERTIFICATE_NUMBER: certificate_number,
        FIELD_KEY_DIPLOMA: "",
        FIELD_KEY_COURSE: course.title,
        FIELD_KEY_COURSE_VERSION: enrollment.course_version.label,
        FIELD_KEY_SEMESTER: "",
        FIELD_KEY_COHORT: "",
        FIELD_KEY_GRADUATION_DATE: completion_date.strftime("%Y-%m-%d") if completion_date else "",
    }


ACTIVE_CERTIFICATE_STATUSES = (
    CERTIFICATE_STATUS_GENERATED,
    CERTIFICATE_STATUS_AVAILABLE,
    CERTIFICATE_STATUS_REGENERATION,
)


def _certificate_completion_date(completion: StudentDiplomaCompletion):
    return completion.completed_at or completion.graduated_at


def _reserve_certificate_row(
    *,
    student: StudentProfile,
    template: CertificateTemplate,
    scope_filter: dict,
    number_factory,
    actor,
    metadata: dict,
    regeneration_cert_id: int | None = None,
) -> GeneratedCertificate:
    """Reserve the immutable certificate row and number in a short transaction.

    Slow PDF rendering and storage work stays outside this helper. A previously
    failed certificate is reused so a retry never allocates a second number.
    """
    existing_active = GeneratedCertificate.objects.select_for_update().filter(
        status__in=ACTIVE_CERTIFICATE_STATUSES,
        **scope_filter,
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
        status=CERTIFICATE_STATUS_FAILED,
        **scope_filter,
    ).order_by("-generated_at").first()
    if cert is None:
        cert = GeneratedCertificate.objects.create(
            student=student,
            certificate_template=template,
            certificate_number=number_factory(),
            status=CERTIFICATE_STATUS_GENERATED,
            generated_by=actor,
            metadata_snapshot=metadata,
            **scope_filter,
        )
    else:
        cert.status = CERTIFICATE_STATUS_GENERATED
        cert.certificate_template = template
        cert.generated_by = actor
        cert.save(update_fields=["status", "certificate_template", "generated_by", "updated_at"])
    return cert


def _render_and_store_certificate(
    cert: GeneratedCertificate,
    template: CertificateTemplate,
    field_values: dict[str, str],
    *,
    key_prefix: str,
    html_factory,
) -> GeneratedCertificate:
    uploaded_key = ""
    try:
        if (
            template.source_status == TEMPLATE_SOURCE_STATUS_READY
            and template.source_storage_key
            and template.field_mapping
        ):
            source_bytes = storage_service.download_file(template.source_storage_key)
            output_bytes = overlay_text_on_source(
                source_bytes,
                template.source_format,
                template.field_mapping,
                field_values,
            )
            pdf_bytes = (
                output_bytes
                if template.source_format == "pdf"
                else _image_bytes_to_pdf(output_bytes)
            )
        else:
            pdf_bytes = _html_to_pdf(html_factory())

        result = storage_service.upload_file(
            data=pdf_bytes,
            key_prefix=key_prefix,
            content_type="application/pdf",
            max_size=10 * 1024 * 1024,
            allowed_types=[".pdf"],
            original_filename=f"certificate_{cert.certificate_number}.pdf",
        )
        uploaded_key = result.key

        snapshot = dict(cert.metadata_snapshot or {})
        snapshot.pop("error", None)
        snapshot["student_name"] = field_values.get(FIELD_KEY_STUDENT_NAME, "")
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


def _render_diploma_certificate(
    cert: GeneratedCertificate,
    student: StudentProfile,
    enrollment: DiplomaEnrollment,
    completion: StudentDiplomaCompletion,
    template: CertificateTemplate,
) -> GeneratedCertificate:
    field_values = resolve_certificate_field_values(
        student,
        enrollment,
        completion,
        cert.certificate_number,
    )
    return _render_and_store_certificate(
        cert,
        template,
        field_values,
        key_prefix=f"certificates/{enrollment.id}",
        html_factory=lambda: render_certificate_html(
            student,
            template,
            completion,
            cert.certificate_number,
        ),
    )


def _render_course_certificate(
    cert: GeneratedCertificate,
    student: StudentProfile,
    enrollment: CourseEnrollment,
    template: CertificateTemplate,
) -> GeneratedCertificate:
    field_values = resolve_course_certificate_field_values(
        student,
        enrollment,
        cert.certificate_number,
    )
    return _render_and_store_certificate(
        cert,
        template,
        field_values,
        key_prefix=f"certificates/course/{enrollment.id}",
        html_factory=lambda: render_course_certificate_html(
            student,
            template,
            enrollment,
            cert.certificate_number,
        ),
    )


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

    with transaction.atomic():
        locked_enrollment = DiplomaEnrollment.objects.select_for_update().select_related(
            "cohort__diploma",
            "student__user",
        ).get(pk=enrollment.pk)
        Diploma.objects.select_for_update().get(pk=locked_enrollment.cohort.diploma_id)
        template = CertificateTemplate.objects.filter(
            diploma=locked_enrollment.cohort.diploma,
            active=True,
        ).first()
        if not template:
            template = _create_placeholder_template(locked_enrollment, actor)
        completion_date = _certificate_completion_date(completion)
        cert = _reserve_certificate_row(
            student=student,
            template=template,
            scope_filter={"diploma_enrollment": locked_enrollment},
            number_factory=lambda: get_next_certificate_number(
                locked_enrollment.cohort.diploma_id,
                student.id,
            ),
            actor=actor,
            metadata={
                "student_name": student.user.full_name,
                "program_type": "diploma",
                "program_title": locked_enrollment.cohort.diploma.title,
                "diploma": locked_enrollment.cohort.diploma.title,
                "cohort": locked_enrollment.cohort.name,
                "completed_at": completion_date.isoformat() if completion_date else None,
                "mapping_schema_version": template.mapping_schema_version,
            },
            regeneration_cert_id=regeneration_cert_id,
        )

    return _render_diploma_certificate(
        cert,
        student,
        locked_enrollment,
        completion,
        template,
    )


def generate_course_certificate_pdf(
    student: StudentProfile,
    enrollment: CourseEnrollment,
    *,
    actor=None,
    regeneration_cert_id: int | None = None,
) -> GeneratedCertificate:
    if enrollment.status != ENROLLMENT_STATUS_COMPLETED:
        raise ValueError(_("The course enrollment is not completed."))

    with transaction.atomic():
        locked_enrollment = CourseEnrollment.objects.select_for_update().select_related(
            "course_version__course",
            "student__user",
        ).get(pk=enrollment.pk)
        if locked_enrollment.status != ENROLLMENT_STATUS_COMPLETED:
            raise ValueError(_("The course enrollment is not completed."))
        course = locked_enrollment.course_version.course
        Course.objects.select_for_update().get(pk=course.id)
        template = CertificateTemplate.objects.filter(
            course=course,
            active=True,
        ).first()
        if not template:
            template = _create_course_placeholder_template(locked_enrollment, actor)
        completion_date = locked_enrollment.completed_at or locked_enrollment.updated_at
        cert = _reserve_certificate_row(
            student=student,
            template=template,
            scope_filter={"course_enrollment": locked_enrollment},
            number_factory=lambda: get_next_course_certificate_number(
                course.id,
                student.id,
            ),
            actor=actor,
            metadata={
                "student_name": student.user.full_name,
                "program_type": "course",
                "program_title": course.title,
                "course": course.title,
                "course_version": locked_enrollment.course_version.label,
                "completed_at": completion_date.isoformat() if completion_date else None,
                "mapping_schema_version": template.mapping_schema_version,
            },
            regeneration_cert_id=regeneration_cert_id,
        )

    return _render_course_certificate(
        cert,
        student,
        locked_enrollment,
        template,
    )


def _record_certificate_issued(
    cert: GeneratedCertificate,
    student: StudentProfile,
    *,
    actor,
    source: str,
    scope: str,
    enrollment_id: int,
) -> None:
    snapshot = cert.metadata_snapshot or {}
    record_audit_event(
        actor=actor,
        action=ACTION_CERTIFICATE_GENERATED,
        target_type="certificate",
        target_id=str(cert.id),
        section=SECTION_CERTIFICATES,
        target_label=f"{cert.certificate_number} · {student.full_name}",
        subject_type="student",
        subject_id=str(student.id),
        subject_label=student.full_name,
        metadata={
            "certificate_number": cert.certificate_number,
            "student_id": student.id,
            "enrollment_id": enrollment_id,
            "scope": scope,
            "program_title": (
                snapshot.get("program_title")
                or snapshot.get("diploma")
                or snapshot.get("course")
                or ""
            ),
            "status": cert.status,
            "source": source,
        },
    )


def issue_certificate(
    student: StudentProfile,
    enrollment: DiplomaEnrollment,
    *,
    actor=None,
    source: str = "manual",
) -> GeneratedCertificate:
    """Issue or retry the single active certificate for a diploma enrollment.

    Shared by the explicit admin action and automatic issuance when an admin
    marks completion/graduation. ``ValueError`` from generation (not completed,
    already active) propagates before any audit event is written.
    """
    cert = generate_certificate_pdf(student, enrollment, actor=actor)
    _record_certificate_issued(
        cert,
        student,
        actor=actor,
        source=source,
        scope="diploma",
        enrollment_id=enrollment.id,
    )
    return cert


def issue_course_certificate(
    student: StudentProfile,
    enrollment: CourseEnrollment,
    *,
    actor=None,
    source: str = "manual",
) -> GeneratedCertificate:
    """Issue or retry the single active certificate for a completed course."""
    cert = generate_course_certificate_pdf(student, enrollment, actor=actor)
    _record_certificate_issued(
        cert,
        student,
        actor=actor,
        source=source,
        scope="course",
        enrollment_id=enrollment.id,
    )
    return cert


def _image_bytes_to_pdf(png_bytes: bytes) -> bytes:
    """Wrap a PNG (raster overlay output) into a single-page PDF via PIL."""
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
    template, _created = CertificateTemplate.objects.update_or_create(
        diploma=enrollment.cohort.diploma,
        language="ar",
        template_type="completion",
        defaults={
            "name": f"Placeholder - {enrollment.cohort.diploma.title}",
            "active": True,
            "is_placeholder": True,
            # A truthy design switches the renderer to the visual defaults while
            # preserving any legacy body_html that may already exist.
            "design": {"version": 1},
            "created_by": actor or enrollment.student.user,
        },
    )
    return template


def _create_course_placeholder_template(
    enrollment: CourseEnrollment,
    actor,
) -> CertificateTemplate:
    course = enrollment.course_version.course
    template, _created = CertificateTemplate.objects.update_or_create(
        course=course,
        language="ar",
        template_type="completion",
        defaults={
            "name": f"Placeholder - {course.title}",
            "active": True,
            "is_placeholder": True,
            "design": {"version": 1},
            "created_by": actor or enrollment.student.user,
        },
    )
    return template


def ingest_certificate_logo(
    template: CertificateTemplate,
    *,
    data: bytes,
    filename: str,
    content_type: str = "",
) -> CertificateTemplate:
    """Validate and store a bounded PNG/JPG logo for the HTML designer."""
    mime_type = validate_logo_bytes(data, filename)
    old_key = template.logo_storage_key
    result = storage_service.upload_file(
        data=data,
        key_prefix=f"certificate_templates/{template.id}/logo",
        content_type=mime_type,
        max_size=MAX_LOGO_BYTES,
        allowed_types=[".png", ".jpg", ".jpeg"],
        original_filename=filename,
    )
    template.logo_storage_key = result.key
    template.logo_content_type = mime_type
    template.logo_byte_size = result.byte_size
    try:
        template.save(update_fields=[
            "logo_storage_key", "logo_content_type", "logo_byte_size", "updated_at",
        ])
    except Exception:
        try:
            if not storage_service.delete_file(result.key):
                record_deferred_storage_deletion(
                    key=result.key,
                    error="Certificate logo database update failed",
                )
        except Exception as cleanup_exc:
            record_deferred_storage_deletion(key=result.key, error=str(cleanup_exc))
        raise
    if old_key and old_key != result.key:
        try:
            if not storage_service.delete_file(old_key):
                record_deferred_storage_deletion(key=old_key, error="Certificate logo replaced")
        except Exception as exc:
            record_deferred_storage_deletion(key=old_key, error=str(exc))
    return template


def clear_certificate_logo(template: CertificateTemplate) -> CertificateTemplate:
    """Remove a template's logo while preserving generated certificates."""
    old_key = template.logo_storage_key
    template.logo_storage_key = ""
    template.logo_content_type = ""
    template.logo_byte_size = None
    template.save(update_fields=[
        "logo_storage_key", "logo_content_type", "logo_byte_size", "updated_at",
    ])
    if old_key:
        try:
            if not storage_service.delete_file(old_key):
                record_deferred_storage_deletion(key=old_key, error="Certificate logo cleared")
        except Exception as exc:
            record_deferred_storage_deletion(key=old_key, error=str(exc))
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
    """Validate, store, preview, and map a PDF/PSD/JPG certificate source (P7-02).

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
        allowed_types=[".pdf", ".psd", ".jpg", ".jpeg"],
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


def _schedule_certificate_template_storage_cleanup(storage_keys) -> None:
    """Remove template artifacts after the database delete commits."""
    keys = tuple(dict.fromkeys(key for key in storage_keys if key))

    def _cleanup():
        for key in keys:
            try:
                if not storage_service.delete_file(key):
                    record_deferred_storage_deletion(
                        key=key,
                        error="Certificate template deleted",
                    )
            except Exception as exc:  # noqa: BLE001 - cleanup is best-effort post-commit
                logger.exception("Failed to clean certificate template object %s", key)
                record_deferred_storage_deletion(key=key, error=str(exc))

    transaction.on_commit(_cleanup)


def delete_certificate_template(*, template: CertificateTemplate, actor) -> None:
    """Delete an inactive template while preserving generated certificate history."""
    with transaction.atomic():
        locked = CertificateTemplate.objects.select_for_update().get(pk=template.pk)
        if locked.active:
            raise ValueError(_("Deactivate the template before deleting it."))

        storage_keys = (
            locked.source_storage_key,
            locked.preview_storage_key,
            locked.logo_storage_key,
        )
        program_title = (
            locked.diploma.title if locked.diploma_id
            else locked.course.title if locked.course_id else ""
        )
        record_audit_event(
            actor=actor,
            action=ACTION_CERTIFICATE_TEMPLATE_DELETED,
            target_type="certificate_template",
            target_id=str(locked.id),
            section=SECTION_CERTIFICATES,
            target_label=(
                f"{locked.name} · {program_title}" if program_title else locked.name
            ),
            metadata={
                "name": locked.name,
                "diploma_id": locked.diploma_id,
                "course_id": locked.course_id,
                "program_title": program_title,
                "language": locked.language,
                "template_type": locked.template_type,
                "source_format": locked.source_format,
            },
        )
        locked.delete()
        _schedule_certificate_template_storage_cleanup(storage_keys)


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
    """Replace an available certificate with a new immutable row and number."""
    with transaction.atomic():
        locked = GeneratedCertificate.objects.select_for_update(of=("self",)).select_related(
            "student",
            "diploma_enrollment__cohort__diploma",
            "course_enrollment__course_version__course",
        ).get(pk=cert.pk)
        if locked.status != CERTIFICATE_STATUS_AVAILABLE:
            raise ValueError(_("Only an available certificate can be regenerated."))

        if locked.diploma_enrollment_id:
            scope = "diploma"
            enrollment = locked.diploma_enrollment
            DiplomaEnrollment.objects.select_for_update().get(pk=enrollment.pk)
            completion = StudentDiplomaCompletion.objects.filter(
                diploma_enrollment=enrollment,
                status__in=[DIPLOMA_COMPLETION_COMPLETED, DIPLOMA_COMPLETION_GRADUATED],
            ).first()
            if not completion:
                raise ValueError(_("Student has not completed/graduated yet."))
            diploma = enrollment.cohort.diploma
            template = CertificateTemplate.objects.filter(
                diploma=diploma,
                active=True,
            ).first()
            if not template:
                template = _create_placeholder_template(enrollment, actor)
            def number_factory():
                return get_next_certificate_number(diploma.id, locked.student_id)
            metadata = {
                "student_name": locked.student.user.full_name,
                "program_type": "diploma",
                "program_title": diploma.title,
                "diploma": diploma.title,
                "cohort": enrollment.cohort.name,
                "completed_at": (
                    _certificate_completion_date(completion).isoformat()
                    if _certificate_completion_date(completion) else None
                ),
                "mapping_schema_version": template.mapping_schema_version,
            }
            scope_kwargs = {"diploma_enrollment": enrollment}
        elif locked.course_enrollment_id:
            scope = "course"
            enrollment = locked.course_enrollment
            CourseEnrollment.objects.select_for_update().get(pk=enrollment.pk)
            if enrollment.status != ENROLLMENT_STATUS_COMPLETED:
                raise ValueError(_("The course enrollment is not completed."))
            course = enrollment.course_version.course
            template = CertificateTemplate.objects.filter(
                course=course,
                active=True,
            ).first()
            if not template:
                template = _create_course_placeholder_template(enrollment, actor)
            def number_factory():
                return get_next_course_certificate_number(
                    course.id,
                    locked.student_id,
                )
            metadata = {
                "student_name": locked.student.user.full_name,
                "program_type": "course",
                "program_title": course.title,
                "course": course.title,
                "course_version": enrollment.course_version.label,
                "completed_at": (
                    (enrollment.completed_at or enrollment.updated_at).isoformat()
                ),
                "mapping_schema_version": template.mapping_schema_version,
            }
            scope_kwargs = {"course_enrollment": enrollment}
        else:
            raise ValueError(_("This historical certificate is no longer linked to an enrollment."))

        previous_certificate_id = locked.id
        previous_certificate_number = locked.certificate_number
        locked.status = CERTIFICATE_STATUS_SUPERSEDED
        locked.superseded_at = timezone.now()
        locked.superseded_by = actor
        locked.supersede_reason = reason
        locked.save(
            update_fields=[
                "status", "superseded_at", "superseded_by", "supersede_reason", "updated_at",
            ],
        )

        new_cert = GeneratedCertificate.objects.create(
            student=locked.student,
            certificate_template=template,
            certificate_number=number_factory(),
            status=CERTIFICATE_STATUS_GENERATED,
            generated_by=actor,
            metadata_snapshot={
                **metadata,
                "regenerated_from": previous_certificate_number,
                "previous_certificate_id": previous_certificate_id,
                "regeneration_reason": reason,
            },
            **scope_kwargs,
        )

    if scope == "diploma":
        new_cert = _render_diploma_certificate(
            new_cert,
            locked.student,
            enrollment,
            completion,
            template,
        )
    else:
        new_cert = _render_course_certificate(
            new_cert,
            locked.student,
            enrollment,
            template,
        )

    snapshot = new_cert.metadata_snapshot or {}
    record_audit_event(
        actor=actor,
        action=ACTION_CERTIFICATE_REGENERATED,
        target_type="certificate",
        target_id=str(new_cert.id),
        section=SECTION_CERTIFICATES,
        target_label=f"{new_cert.certificate_number} · {locked.student.full_name}",
        subject_type="student",
        subject_id=str(new_cert.student_id),
        subject_label=locked.student.full_name,
        metadata={
            "certificate_number": new_cert.certificate_number,
            "previous_cert_id": previous_certificate_id,
            "previous_certificate_number": previous_certificate_number,
            "new_certificate_number": new_cert.certificate_number,
            "student_id": new_cert.student_id,
            "scope": scope,
            "program_title": (
                snapshot.get("program_title")
                or snapshot.get("diploma")
                or snapshot.get("course")
                or ""
            ),
            "reason": reason,
            "status": new_cert.status,
        },
    )
    return new_cert
