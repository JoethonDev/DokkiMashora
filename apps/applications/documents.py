"""Document request/upload workflow for diploma applications."""

from __future__ import annotations

import hashlib
import io
import os
import secrets
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from PIL import Image, ImageOps

from apps.communications.services import send_templated_email
from apps.storage.services import FileValidationError, storage_service

from .constants import (
    DECISION_ACTION_REQUEST_DOCUMENTS,
    DECISION_ACTION_SUBMIT_DOCUMENTS,
    DOCUMENT_REQUEST_STATUS_CANCELLED,
    DOCUMENT_REQUEST_STATUS_REQUESTED,
    DOCUMENT_REQUEST_STATUS_SUBMITTED,
    EMAIL_ACTION_DIPLOMA_DOCUMENT_UPLOAD_REQUEST,
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_DOCUMENTS_REQUESTED,
    PROGRAM_APP_STATUS_DOCUMENTS_SUBMITTED,
    PROGRAM_TYPE_DIPLOMA,
)
from .models import (
    ApplicationDocumentRequest,
    ApplicationDocumentUpload,
    ProgramApplication,
    ProgramApplicationDecision,
    ProgramApplicationEmailLog,
    ProgramApplicationNote,
)

if TYPE_CHECKING:
    from django.http import HttpRequest

PERSONA_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ATTACHMENT_ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
PERSONA_MAX_MB = settings.APPLICATION_DOCUMENT_PERSONA_MAX_MB
ATTACHMENTS_MAX_MB = settings.APPLICATION_DOCUMENT_ATTACHMENTS_MAX_MB
MAX_ATTACHMENT_FILES = settings.APPLICATION_DOCUMENT_MAX_ATTACHMENT_FILES
IMAGE_MAX_PIXELS = settings.APPLICATION_DOCUMENT_IMAGE_MAX_PIXELS

_EXTENSION_TO_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
}


def _generate_document_token() -> str:
    return secrets.token_urlsafe(48)


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _get_storage_key_prefix(application: ProgramApplication) -> str:
    return f"applications/{application.pk}/documents"


def validate_persona_image(file) -> bytes:
    """Validate persona image file and return its bytes."""
    data = file.read()
    if not data:
        raise FileValidationError(_("Persona image is empty."))
    ext = _get_extension(file.name)
    persona_max_bytes = PERSONA_MAX_MB * 1024 * 1024
    _validate_file_common(data, ext, persona_max_bytes, PERSONA_ALLOWED_EXTENSIONS)
    if ext in {".jpg", ".jpeg", ".png", ".webp"}:
        _validate_image(data)
    return data


def validate_attachment_files(files) -> dict[str, Any]:
    """Validate attachment files. Returns dict with validation result.

    Returns:
        {"valid": True, "payload": {"files": [(data, name, ext), ...],
         "is_pdf": bool, "file_count": int}}
        or {"valid": False, "error": str}
    """
    if not files:
        return {"valid": False, "error": _("At least one attachment file is required.")}

    file_count = len(files)
    if file_count > MAX_ATTACHMENT_FILES:
        return {"valid": False, "error": _("Maximum %(max)d attachment files allowed.") % {"max": MAX_ATTACHMENT_FILES}}

    has_pdf = False
    has_image = False
    payloads = []
    total_size = 0
    attachments_max_bytes = ATTACHMENTS_MAX_MB * 1024 * 1024

    for f in files:
        data = f.read()
        if not data:
            return {"valid": False, "error": _("One of the attachment files is empty.")}
        ext = _get_extension(f.name)
        if ext not in ATTACHMENT_ALLOWED_EXTENSIONS:
            return {"valid": False, "error": _("File type '%(ext)s' is not allowed for attachments.") % {"ext": ext}}
        # Per-file size check
        if len(data) > attachments_max_bytes:
            return {"valid": False, "error": _("File '%(name)s' exceeds %(max)s MB.") % {"name": f.name, "max": ATTACHMENTS_MAX_MB}}
        # MIME type check
        expected_mime = _EXTENSION_TO_MIME.get(ext)
        if expected_mime and getattr(f, "content_type", None) and f.content_type != expected_mime:
            return {"valid": False, "error": _("File '%(name)s' has incorrect content type.") % {"name": f.name}}
        # PDF header check
        if ext == ".pdf" and not data.startswith(b"%PDF-"):
            return {"valid": False, "error": _("File '%(name)s' is not a valid PDF.") % {"name": f.name}}
        if ext == ".pdf" and has_image:
            return {"valid": False, "error": _("Cannot mix PDF and image files in attachments.")}
        if ext == ".pdf":
            has_pdf = True
        else:
            has_image = True
            _validate_image(data)
        if ext != ".pdf" and has_pdf:
            return {"valid": False, "error": _("Cannot mix image and PDF files in attachments.")}
        total_size += len(data)
        if total_size > attachments_max_bytes:
            return {"valid": False, "error": _("Total attachment size exceeds %(max)s MB.") % {"max": ATTACHMENTS_MAX_MB}}
        payloads.append((data, f.name, ext))

    if file_count == 0:
        return {"valid": False, "error": _("At least one attachment file is required.")}

    return {"valid": True, "payload": {"files": payloads, "is_pdf": has_pdf, "file_count": file_count}}


def _get_extension(filename: str) -> str:
    _, ext = os.path.splitext(filename)
    return ext.lower()


def _validate_file_common(data: bytes, ext: str, max_bytes: int, allowed_exts: set[str]) -> None:
    if ext not in allowed_exts:
        raise FileValidationError(
            _("File type '%(ext)s' is not allowed. Allowed: %(allowed)s") % {
                "ext": ext, "allowed": ", ".join(sorted(allowed_exts)),
            }
        )
    if not data:
        raise FileValidationError(_("Uploaded file is empty."))
    if len(data) > max_bytes:
        raise FileValidationError(
            _("File size exceeds the maximum allowed size of %(max_size_mb)s MB.") % {
                "max_size_mb": max_bytes // (1024 * 1024),
            }
        )


def _validate_image(data: bytes) -> None:
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
    except Exception as exc:
        raise FileValidationError(_("Uploaded image is corrupted or unsupported.")) from exc
    img = Image.open(io.BytesIO(data))
    width, height = img.size
    if width * height > IMAGE_MAX_PIXELS:
        raise FileValidationError(
            _("Image dimensions (%(w)dx%(h)d) exceed the maximum allowed %(max)s pixels.") % {
                "w": width, "h": height, "max": IMAGE_MAX_PIXELS,
            }
        )


def combine_images_to_pdf(files: list[tuple[bytes, str, str]]) -> bytes:
    """Combine image files into one PDF. Returns PDF bytes."""
    images = []
    for data, _name, _ext in files:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")
        images.append(img)

    output = io.BytesIO()
    if images:
        images[0].save(output, format="PDF", save_all=True, append_images=images[1:])
    else:
        raise FileValidationError(_("No valid images to combine."))
    return output.getvalue()


# ---------------------------------------------------------------------------
# Public service API
# ---------------------------------------------------------------------------


def request_documents_program_application(
    application, actor, *, expires_at, notes="", send_email=True,
) -> ApplicationDocumentRequest:
    """Request documents for an accepted diploma application."""
    with transaction.atomic():
        locked = ProgramApplication.objects.select_for_update().get(pk=application.pk)

        if locked.program_type != PROGRAM_TYPE_DIPLOMA:
            raise ValueError(_("Document requests are only for diploma applications."))
        if locked.status not in (PROGRAM_APP_STATUS_ACCEPTED, PROGRAM_APP_STATUS_DOCUMENTS_REQUESTED):
            raise ValueError(_("Documents can only be requested for accepted applications."))
        if not locked.cohort:
            raise ValueError(_("Application has no cohort target."))
        if not locked.student.user.email_activated_at:
            raise ValueError(_("Student account must be activated before requesting documents."))
        if timezone.is_naive(expires_at):
            raise ValueError(_("Expiry time must be timezone-aware."))
        if expires_at <= timezone.now():
            raise ValueError(_("Expiry time must be in the future."))

        # Cancel any existing open request
        ApplicationDocumentRequest.objects.filter(
            program_application=locked,
            status=DOCUMENT_REQUEST_STATUS_REQUESTED,
        ).update(
            status=DOCUMENT_REQUEST_STATUS_CANCELLED,
            cancelled_at=timezone.now(),
            cancelled_by=actor,
        )

        raw_token = _generate_document_token()
        token_hash = _hash_token(raw_token)

        doc_request = ApplicationDocumentRequest.objects.create(
            program_application=locked,
            status=DOCUMENT_REQUEST_STATUS_REQUESTED,
            token_hash=token_hash,
            expires_at=expires_at,
            requested_by=actor,
            requested_at=timezone.now(),
        )

        # Set application status
        old_status = locked.status
        locked.status = PROGRAM_APP_STATUS_DOCUMENTS_REQUESTED
        locked.save(update_fields=["status", "updated_at"])

        # Record decision
        ProgramApplicationDecision.objects.create(
            program_application=locked,
            action=DECISION_ACTION_REQUEST_DOCUMENTS,
            old_status=old_status,
            new_status=PROGRAM_APP_STATUS_DOCUMENTS_REQUESTED,
            actor=actor,
            metadata={
                "document_request_id": doc_request.pk,
                "expires_at": expires_at.isoformat(),
            },
        )

        if notes:
            ProgramApplicationNote.objects.create(
                program_application=locked,
                actor=actor,
                note=notes,
                metadata={"document_request_id": doc_request.pk},
            )

        if send_email:
            transaction.on_commit(
                lambda: _send_document_upload_request_email(locked, doc_request, raw_token)
            )

    return doc_request


def get_document_request_by_token(raw_token: str) -> ApplicationDocumentRequest | None:
    """Look up a document request by raw token (constant-time comparison friendly)."""
    token_hash = _hash_token(raw_token)
    return ApplicationDocumentRequest.objects.filter(
        token_hash=token_hash,
    ).select_related(
        "program_application__student__user",
        "program_application__cohort__diploma",
    ).first()


def submit_application_documents(
    request_obj: ApplicationDocumentRequest, actor, *, persona_file, attachment_files,
) -> ApplicationDocumentUpload:
    """Submit persona image and attachments for a document request."""
    application = request_obj.program_application

    # Re-read and lock
    with transaction.atomic():
        locked_request = ApplicationDocumentRequest.objects.select_for_update().get(pk=request_obj.pk)
        locked_app = ProgramApplication.objects.select_for_update().get(pk=application.pk)

        if locked_request.status != DOCUMENT_REQUEST_STATUS_REQUESTED:
            raise ValueError(_("This document request is no longer open."))
        if locked_request.expires_at <= timezone.now():
            raise ValueError(_("This document request has expired."))
        if locked_app.program_type != PROGRAM_TYPE_DIPLOMA:
            raise ValueError(_("Not a diploma application."))
        if locked_app.status != PROGRAM_APP_STATUS_DOCUMENTS_REQUESTED:
            raise ValueError(_("Application is not in documents-requested state."))
        if locked_app.student.user != actor:
            raise ValueError(_("You can only upload documents for your own application."))

        # Validate persona
        persona_data = validate_persona_image(persona_file)
        persona_ext = _get_extension(persona_file.name)
        persona_prefix = _get_storage_key_prefix(locked_app)
        persona_key = _upload_file_safe(persona_data, persona_prefix, persona_file.name, _EXTENSION_TO_MIME[persona_ext])

        # Validate attachments — clean up persona if validation fails
        result = validate_attachment_files(attachment_files)
        if not result["valid"]:
            storage_service.delete_file(persona_key)
            raise FileValidationError(result["error"])

        payload = result["payload"]

        # Convert / upload attachments — clean up persona on failure
        if payload["is_pdf"]:
            pdf_data = payload["files"][0][0]
        else:
            pdf_data = combine_images_to_pdf(payload["files"])

        try:
            attachments_key = _upload_file_safe(
                pdf_data, persona_prefix, "attachments.pdf", "application/pdf",
            )
        except Exception:
            storage_service.delete_file(persona_key)
            raise

        # DB writes — clean up both files if database operations fail
        try:
            upload = ApplicationDocumentUpload.objects.create(
                request=locked_request,
                submitted_by=actor,
                submitted_at=timezone.now(),
                persona_image_storage_key=persona_key,
                persona_image_original_filename=persona_file.name,
                persona_image_content_type=_EXTENSION_TO_MIME[persona_ext],
                persona_image_file_size=len(persona_data),
                persona_image_checksum=hashlib.sha256(persona_data).hexdigest(),
                attachments_pdf_storage_key=attachments_key,
                attachments_original_filenames=[f[1] for f in payload["files"]],
                attachments_file_size=len(pdf_data),
                attachments_checksum=hashlib.sha256(pdf_data).hexdigest(),
                original_file_count=payload["file_count"],
            )

            # Mark request as submitted
            locked_request.status = DOCUMENT_REQUEST_STATUS_SUBMITTED
            locked_request.submitted_at = timezone.now()
            locked_request.save(update_fields=["status", "submitted_at", "updated_at"])

            # Set application status
            old_status = locked_app.status
            locked_app.status = PROGRAM_APP_STATUS_DOCUMENTS_SUBMITTED
            locked_app.save(update_fields=["status", "updated_at"])

            # Record decision
            ProgramApplicationDecision.objects.create(
                program_application=locked_app,
                action=DECISION_ACTION_SUBMIT_DOCUMENTS,
                old_status=old_status,
                new_status=PROGRAM_APP_STATUS_DOCUMENTS_SUBMITTED,
                actor=actor,
                metadata={
                    "document_request_id": locked_request.pk,
                    "document_upload_id": upload.pk,
                    "original_file_count": payload["file_count"],
                    "persona_file_size": len(persona_data),
                    "attachments_file_size": len(pdf_data),
                },
            )
        except Exception:
            storage_service.delete_file(persona_key)
            storage_service.delete_file(attachments_key)
            raise

    # Try to activate (outside the transaction since it succeeds independently)
    from .services import activate_application_if_ready
    activate_application_if_ready(locked_app, actor=actor)

    return upload


def _upload_file_safe(data: bytes, prefix: str, filename: str, content_type: str) -> str:
    """Upload a file through storage_service with Phase 4 defaults."""
    result = storage_service.upload_file(
        data,
        key_prefix=prefix,
        content_type=content_type,
        max_size=ATTACHMENTS_MAX_MB * 1024 * 1024,
        allowed_types=list(ATTACHMENT_ALLOWED_EXTENSIONS),
        original_filename=filename,
    )
    return result.key


def has_application_documents_submitted(application) -> bool:
    """Check if a complete document upload exists for this application."""
    return ApplicationDocumentUpload.objects.filter(
        request__program_application=application,
    ).exists()


def get_latest_document_request(application) -> ApplicationDocumentRequest | None:
    return ApplicationDocumentRequest.objects.filter(
        program_application=application,
    ).select_related("upload").order_by("-requested_at").first()


def get_application_document_upload(application) -> ApplicationDocumentUpload | None:
    return ApplicationDocumentUpload.objects.filter(
        request__program_application=application,
    ).select_related("request").first()


def build_document_upload_url(
    document_request: ApplicationDocumentRequest, raw_token: str, request: HttpRequest | None = None,
) -> str:
    """Build the absolute expiring upload URL for a document request."""
    path = reverse("applications:document_upload", kwargs={"token": raw_token})
    return f"{settings.SITE_URL.rstrip('/')}{path}"


def _send_document_upload_request_email(application, doc_request, raw_token) -> None:
    """Send the document upload request email."""
    email = application.student.user.email
    if not email:
        return
    cohort = application.cohort
    path = reverse("applications:document_upload", kwargs={"token": raw_token})
    upload_url = f"{settings.SITE_URL.rstrip('/')}{path}"
    context = {
        "full_name": application.student.full_name or "",
        "diploma_title": cohort.diploma.title if cohort and cohort.diploma else "",
        "cohort_name": cohort.name if cohort else "",
        "upload_url": upload_url,
        "expires_at": doc_request.expires_at.isoformat(),
        "staff_phone": "",
    }
    send_templated_email(EMAIL_ACTION_DIPLOMA_DOCUMENT_UPLOAD_REQUEST, email, context, sent_by=doc_request.requested_by)
    existing = ProgramApplicationEmailLog.objects.filter(
        program_application=application,
        action=EMAIL_ACTION_DIPLOMA_DOCUMENT_UPLOAD_REQUEST,
        status="sent",
        is_resend=False,
    ).exists()
    ProgramApplicationEmailLog.objects.create(
        program_application=application,
        action=EMAIL_ACTION_DIPLOMA_DOCUMENT_UPLOAD_REQUEST,
        template_slug=EMAIL_ACTION_DIPLOMA_DOCUMENT_UPLOAD_REQUEST,
        recipient=email,
        sent_by=doc_request.requested_by,
        sent_at=timezone.now(),
        status="sent",
        is_resend=existing,
    )
