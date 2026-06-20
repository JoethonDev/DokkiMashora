"""Tests for the storage app services."""

import hashlib
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings

from apps.storage.services import (
    FileValidationError,
    LocalStorageBackend,
    StorageError,
    StorageService,
    UploadResult,
    _compute_checksum,
    _validate_file,
    build_storage_key,
)


class TestBuildStorageKey(TestCase):
    def test_returns_key_with_uuid_and_ext(self):
        key = build_storage_key("homework/2024", "essay.pdf")
        self.assertTrue(key.startswith("homework/2024/"))
        self.assertTrue(key.endswith(".pdf"))
        self.assertEqual(len(key.split("/")[-1]), 32 + 4)

    def test_sanitizes_ext_to_lowercase(self):
        key = build_storage_key("uploads", "Photo.JPG")
        self.assertTrue(key.endswith(".jpg"))

    def test_handles_no_ext(self):
        key = build_storage_key("uploads", "readme")
        self.assertFalse(key.endswith("."))


class TestComputeChecksum(TestCase):
    def test_returns_sha256_hex(self):
        data = b"hello"
        expected = hashlib.sha256(data).hexdigest()
        self.assertEqual(_compute_checksum(data), expected)

    def test_different_data_different_hash(self):
        self.assertNotEqual(_compute_checksum(b"foo"), _compute_checksum(b"bar"))


class TestValidateFile(TestCase):
    def test_empty_data_raises(self):
        with self.assertRaises(FileValidationError, msg="empty"):
            _validate_file(b"", "file.pdf", "application/pdf", 1024 * 1024, [".pdf"])

    def test_oversized_raises(self):
        data = b"x" * 100
        with self.assertRaises(FileValidationError, msg="exceeds"):
            _validate_file(data, "file.pdf", "application/pdf", 50, [".pdf"])

    def test_disallowed_type_raises(self):
        data = b"hello"
        with self.assertRaises(FileValidationError, msg="not allowed"):
            _validate_file(data, "file.exe", "application/x-msdownload", 1024 * 1024, [".pdf"])

    def test_allowed_type_passes(self):
        try:
            _validate_file(b"hello", "file.pdf", "application/pdf", 1024 * 1024, [".pdf"])
        except FileValidationError:
            self.fail("FileValidationError raised unexpectedly")

    def test_empty_allowed_types_list_skips_check(self):
        try:
            _validate_file(b"hello", "file.exe", "application/x-msdownload", 1024 * 1024, [])
        except FileValidationError:
            self.fail("FileValidationError raised unexpectedly")


class TestLocalStorageBackend(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.backend = LocalStorageBackend()
        self.backend.base_path = Path(self.temp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_upload_creates_file(self):
        self.backend.upload("test/hello.txt", b"world", "text/plain")
        file_path = self.backend.base_path / "test" / "hello.txt"
        self.assertTrue(file_path.exists())
        self.assertEqual(file_path.read_bytes(), b"world")

    def test_delete_removes_file(self):
        self.backend.upload("test/hello.txt", b"world", "text/plain")
        self.backend.delete("test/hello.txt")
        file_path = self.backend.base_path / "test" / "hello.txt"
        self.assertFalse(file_path.exists())

    def test_delete_nonexistent_does_not_raise(self):
        try:
            self.backend.delete("nonexistent/file.txt")
        except Exception:
            self.fail("delete raised unexpectedly")

    def test_exists_returns_true(self):
        self.backend.upload("exists.txt", b"data", "text/plain")
        self.assertTrue(self.backend.exists("exists.txt"))

    def test_exists_returns_false(self):
        self.assertFalse(self.backend.exists("no-such-file.txt"))

    def test_get_download_url(self):
        url = self.backend.get_download_url("test/file.txt")
        self.assertIn(settings.MEDIA_URL, url)
        self.assertIn("test/file.txt", url)


class TestStorageServiceUpload(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.service = StorageService()
        self.service._backend = LocalStorageBackend()
        self.service._backend.base_path = Path(self.temp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_upload_file_returns_upload_result(self):
        result = self.service.upload_file(
            b"hello world",
            key_prefix="homework",
            content_type="text/plain",
            max_size=1024 * 1024,
            allowed_types=[".txt"],
            original_filename="hello.txt",
        )
        self.assertIsInstance(result, UploadResult)
        self.assertTrue(result.key.startswith("homework/"))
        self.assertEqual(result.content_type, "text/plain")
        self.assertEqual(result.byte_size, 11)
        self.assertEqual(result.checksum, _compute_checksum(b"hello world"))
        self.assertEqual(result.backend, "local")

    def test_upload_file_validates_before_upload(self):
        with self.assertRaises(FileValidationError):
            self.service.upload_file(
                b"x" * 9999,
                key_prefix="homework",
                content_type="text/plain",
                max_size=100,
                allowed_types=[".txt"],
                original_filename="hello.txt",
            )

    def test_delete_file_does_not_raise(self):
        result = self.service.upload_file(
            b"data",
            key_prefix="test",
            content_type="text/plain",
            max_size=1024 * 1024,
            original_filename="f.txt",
        )
        try:
            self.service.delete_file(result.key)
        except Exception:
            self.fail("delete_file raised unexpectedly")

    def test_get_download_url(self):
        result = self.service.upload_file(
            b"data",
            key_prefix="test",
            content_type="text/plain",
            max_size=1024 * 1024,
            original_filename="f.txt",
        )
        url = self.service.get_download_url(result.key)
        self.assertIsInstance(url, str)
        self.assertTrue(url)

    def test_exists(self):
        result = self.service.upload_file(
            b"data",
            key_prefix="test",
            content_type="text/plain",
            max_size=1024 * 1024,
            original_filename="f.txt",
        )
        self.assertTrue(self.service.exists(result.key))
        self.assertFalse(self.service.exists("nonexistent/key"))


class TestProtectedDownloadUrl(TestCase):
    def test_denies_when_permission_fails(self):
        service = StorageService()
        service._backend = LocalStorageBackend()
        with self.assertRaises(StorageError, msg="Access denied."):
            service.get_protected_download_url(
                "some/key",
                user=None,
                permission_fn=lambda u: False,
            )

    def test_returns_url_when_permission_passes(self):
        service = StorageService()
        service._backend = LocalStorageBackend()
        url = service.get_protected_download_url(
            "some/key",
            user=None,
            permission_fn=lambda u: True,
        )
        self.assertIsInstance(url, str)
        self.assertTrue(url)

    @override_settings(PRODUCTION_DEPLOYMENT=True, USE_R2_STORAGE=False)
    def test_fails_closed_in_production_without_r2(self):
        service = StorageService()
        with self.assertRaises(StorageError):
            service.get_protected_download_url(
                "some/key",
                user=None,
                permission_fn=lambda u: True,
            )
