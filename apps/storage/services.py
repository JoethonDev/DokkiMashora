"""Storage service abstraction for local and R2 backends.

Usage:
    from apps.storage.services import storage_service

    # Upload a file
    result = storage_service.upload_file(
        file_bytes, key_prefix="homework/2024/",
        content_type="application/pdf",
        max_size=25 * 1024 * 1024,
        allowed_types=[".pdf", ".docx"],
        original_filename="essay.pdf",
    )

    # Get download URL
    url = storage_service.get_download_url(key, expires_in=3600)

    # Delete
    storage_service.delete_file(key)
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.utils.translation import gettext_lazy as _

try:
    import boto3
except ImportError:
    boto3 = None

logger = logging.getLogger("apps.storage")

_ALLOWED_MIME_TYPES_BY_EXTENSION = {
    ".pdf": {"application/pdf"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    },
}


class StorageError(Exception):
    """Application-level storage exception. Messages are user-safe."""


class FileValidationError(StorageError):
    """Raised when a file fails validation before upload."""


@dataclass
class UploadResult:
    key: str
    content_type: str
    byte_size: int
    checksum: str
    backend: str


def build_storage_key(prefix: str, original_filename: str) -> str:
    """Build a unique storage key from prefix + UUID + sanitized filename."""
    ext = Path(original_filename).suffix.lower()
    unique = uuid.uuid4().hex
    safe_name = f"{unique}{ext}"
    return f"{prefix.rstrip('/')}/{safe_name}"


def _compute_checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_file(
    data: bytes,
    original_filename: str,
    content_type: str,
    max_size: int,
    allowed_types: list[str],
):
    ext = Path(original_filename).suffix.lower()
    if ext not in allowed_types and allowed_types:
        raise FileValidationError(
            _("File type '{file_type}' is not allowed. Allowed: {allowed}").format(
                file_type=ext, allowed=", ".join(allowed_types),
            )
        )
    expected_mime_types = _ALLOWED_MIME_TYPES_BY_EXTENSION.get(ext)
    if expected_mime_types and content_type and content_type not in expected_mime_types:
        raise FileValidationError(
            _("File MIME type '{mime_type}' does not match the uploaded extension '{file_ext}'.").format(
                mime_type=content_type, file_ext=ext,
            )
        )
    if not data:
        raise FileValidationError(_("Uploaded file is empty."))
    if len(data) > max_size:
        raise FileValidationError(
            _("File size exceeds the maximum allowed size of {max_size_mb} MB.").format(
                max_size_mb=max_size // (1024 * 1024),
            )
        )


class LocalStorageBackend:
    """Local filesystem backend for development/testing."""

    def __init__(self):
        self.base_path = Path(settings.MEDIA_ROOT) / "storage"

    def upload(self, key: str, data: bytes, content_type: str) -> None:
        full_path = self.base_path / key
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(data)

    def delete(self, key: str) -> None:
        full_path = self.base_path / key
        if full_path.exists():
            full_path.unlink()

    def get_download_url(self, key: str, expires_in: int | None = None) -> str:
        return f"{settings.MEDIA_URL}storage/{key}"

    def exists(self, key: str) -> bool:
        return (self.base_path / key).exists()

    def download(self, key: str) -> bytes:
        full_path = self.base_path / key
        if not full_path.exists():
            raise StorageError(_("File not found: {key}").format(key=key))
        return full_path.read_bytes()


class R2StorageBackend:
    """Cloudflare R2 (S3-compatible) storage backend."""

    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            if boto3 is None:
                raise ImportError("boto3 is not available")
            self._client = boto3.client(
                "s3",
                endpoint_url=settings.R2_ENDPOINT_URL,
                aws_access_key_id=settings.R2_ACCESS_KEY_ID,
                aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
                region_name="auto",
            )
        return self._client

    def upload(self, key: str, data: bytes, content_type: str) -> None:
        client = self._get_client()
        try:
            client.put_object(
                Bucket=settings.R2_BUCKET_NAME,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
        except Exception as exc:
            logger.error("R2 upload failed for key=%s: %s", key, exc)
            raise StorageError(_("File upload failed. Please try again.")) from exc

    def delete(self, key: str) -> None:
        client = self._get_client()
        try:
            client.delete_object(Bucket=settings.R2_BUCKET_NAME, Key=key)
        except Exception as exc:
            logger.error("R2 delete failed for key=%s: %s", key, exc)

    def get_download_url(self, key: str, expires_in: int | None = None) -> str:
        client = self._get_client()
        expires = expires_in or 3600
        try:
            url = client.generate_presigned_url(
                "get_object",
                Params={"Bucket": settings.R2_BUCKET_NAME, "Key": key},
                ExpiresIn=expires,
            )
            return url
        except Exception as exc:
            logger.error("R2 presigned URL failed for key=%s: %s", key, exc)
            raise StorageError(_("Could not generate download link.")) from exc

    def exists(self, key: str) -> bool:
        client = self._get_client()
        try:
            client.head_object(Bucket=settings.R2_BUCKET_NAME, Key=key)
            return True
        except Exception:
            return False

    def download(self, key: str) -> bytes:
        client = self._get_client()
        try:
            response = client.get_object(Bucket=settings.R2_BUCKET_NAME, Key=key)
            return response["Body"].read()
        except Exception as exc:
            logger.error("R2 download failed for key=%s: %s", key, exc)
            raise StorageError(_("File download failed.")) from exc


class StorageService:
    """Unified storage service with validation and backend abstraction."""

    def __init__(self):
        self._backend: LocalStorageBackend | R2StorageBackend | None = None

    @property
    def backend(self):
        if self._backend is None:
            if settings.USE_R2_STORAGE:
                self._backend = R2StorageBackend()
            else:
                self._backend = LocalStorageBackend()
        return self._backend

    def upload_file(
        self,
        data: bytes,
        *,
        key_prefix: str,
        content_type: str,
        max_size: int,
        allowed_types: list[str] | None = None,
        original_filename: str = "file",
    ) -> UploadResult:
        _validate_file(data, original_filename, content_type, max_size, allowed_types or [])
        key = build_storage_key(key_prefix, original_filename)
        checksum = _compute_checksum(data)
        self.backend.upload(key, data, content_type)
        return UploadResult(
            key=key,
            content_type=content_type,
            byte_size=len(data),
            checksum=checksum,
            backend="r2" if settings.USE_R2_STORAGE else "local",
        )

    def delete_file(self, key: str) -> None:
        self.backend.delete(key)

    def get_download_url(self, key: str, *, expires_in: int | None = None) -> str:
        return self.backend.get_download_url(key, expires_in=expires_in)

    def get_protected_download_url(
        self,
        key: str,
        *,
        user,
        permission_fn,
        expires_in: int | None = None,
    ) -> str:
        """Generate a download URL only after permission check passes.

        ``permission_fn(user)`` must return True for access to be granted.
        """
        if not permission_fn(user):
            raise StorageError(_("Access denied."))
        if getattr(settings, "PRODUCTION_DEPLOYMENT", False) and not settings.USE_R2_STORAGE:
            raise StorageError(_("Protected file storage requires R2 in production."))
        return self.get_download_url(key, expires_in=expires_in)

    def exists(self, key: str) -> bool:
        return self.backend.exists(key)

    def download_file(self, key: str) -> bytes:
        return self.backend.download(key)


storage_service = StorageService()
