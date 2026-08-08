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
import hmac
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from .models import DeferredStorageDeletion

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
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".png": {"image/png"},
    ".webp": {"image/webp"},
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

    def resolve_path(self, key: str) -> Path:
        """Resolve a storage key to a path guaranteed to stay inside base_path.

        Rejects absolute keys, dot segments, and any resolved path that
        escapes the storage root (S2-22). Every key-handling operation goes
        through this containment check.
        """
        if not key or "\x00" in key:
            raise StorageError(_("Invalid storage key."))
        relative = Path(key)
        if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
            raise StorageError(_("Invalid storage key."))
        base = self.base_path.resolve()
        full = (base / relative).resolve()
        if not full.is_relative_to(base):
            raise StorageError(_("Invalid storage key."))
        return full

    def upload(self, key: str, data: bytes, content_type: str) -> None:
        full_path = self.resolve_path(key)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(data)

    def delete(self, key: str) -> bool:
        """Delete a file; returns True when the backend completed the call."""
        full_path = self.resolve_path(key)
        if full_path.exists():
            full_path.unlink()
        return True

    def delete_prefix(self, prefix: str) -> bool:
        """Delete every file under a key prefix (used for HLS output cleanup)."""
        root = self.resolve_path(prefix)
        if not root.is_dir():
            return True
        for child in root.rglob("*"):
            if child.is_file():
                child.unlink()
        for child in sorted(root.rglob("*"), reverse=True):
            if child.is_dir():
                try:
                    child.rmdir()
                except OSError:
                    pass
        return True

    def get_download_url(self, key: str, expires_in: int | None = None) -> str:
        """Return a short-lived, signed proxy URL served by the app.

        Local files are never exposed through an unsigned media URL (S2-21);
        the signature authenticates the key and expiry, mirroring the R2
        presigned-URL bearer semantics.
        """
        self.resolve_path(key)
        expires = int(time.time()) + (expires_in or 3600)
        payload = f"{key}|{expires}"
        sig = hmac.new(settings.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return f"{reverse('storage:protected_download')}?{urlencode({'key': key, 'expires': str(expires), 'sig': sig})}"

    def exists(self, key: str) -> bool:
        try:
            return self.resolve_path(key).exists()
        except StorageError:
            return False

    def download(self, key: str) -> bytes:
        full_path = self.resolve_path(key)
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

    def delete(self, key: str) -> bool:
        """Delete an object; returns True when the backend completed the call.

        S3 DELETE is idempotent, so a success here means the object is gone or
        already absent. Failures are logged with a reconciliation marker so the
        caller can persist a deferred deletion for retry (S2-33).
        """
        client = self._get_client()
        try:
            client.delete_object(Bucket=settings.R2_BUCKET_NAME, Key=key)
            return True
        except Exception as exc:
            logger.error("R2 delete failed for key=%s: %s", key, exc)
            logger.warning(
                "STORAGE_RECONCILIATION: R2 delete failed for key=%s; "
                "the physical object remains and needs manual cleanup: %s",
                key, exc,
            )
            return False

    def delete_prefix(self, prefix: str) -> bool:
        """Delete every object under a key prefix in bounded pages.

        Returns True only when every page was deleted without error.
        """
        client = self._get_client()
        try:
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(
                Bucket=settings.R2_BUCKET_NAME,
                Prefix=prefix,
                PaginationConfig={"PageSize": 1000},
            ):
                keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
                if not keys:
                    continue
                client.delete_objects(
                    Bucket=settings.R2_BUCKET_NAME,
                    Delete={"Objects": keys, "Quiet": True},
                )
            return True
        except Exception as exc:
            logger.error("R2 prefix delete failed for prefix=%s: %s", prefix, exc)
            logger.warning(
                "STORAGE_RECONCILIATION: R2 prefix delete failed for prefix=%s; "
                "objects under this prefix remain and need manual cleanup: %s",
                prefix, exc,
            )
            return False

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

    def delete_file(self, key: str) -> bool:
        """Delete a stored object; returns True when the backend completed it."""
        return self.backend.delete(key)

    def delete_prefix(self, prefix: str) -> bool:
        """Delete every stored object under a key prefix.

        Intended for post-commit cleanup of replaced lecture audio and HLS
        output (S2-33). Returns True only when the backend completed the
        deletion; on failure callers should record a deferred deletion via
        ``record_deferred_storage_deletion`` so reconciliation can retry it.
        The database history is never touched here.
        """
        return self.backend.delete_prefix(prefix)

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


def record_deferred_storage_deletion(*, key: str = "", prefix: str = "", error: str = "") -> DeferredStorageDeletion | None:
    """Persist a failed storage deletion so reconciliation can retry it (S2-33).

    Either ``key`` or ``prefix`` must be supplied. Repeated failures increment
    ``attempts`` and refresh ``last_error`` so operators can see the retry
    history; the row is removed by the reconciliation command once the backend
    accepts the deletion.
    """
    if key:
        obj, created = DeferredStorageDeletion.objects.get_or_create(
            key=key, defaults={"prefix": "", "last_error": error[:500]},
        )
    elif prefix:
        obj, created = DeferredStorageDeletion.objects.get_or_create(
            prefix=prefix, defaults={"key": "", "last_error": error[:500]},
        )
    else:
        return None
    if not created:
        obj.attempts += 1
        obj.last_error = error[:500]
        obj.save(update_fields=["attempts", "last_error", "updated_at"])
    return obj


storage_service = StorageService()
