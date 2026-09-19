"""Tests for audio processing — FFmpeg, HLS metadata, and storage failure."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.academics.constants import AUDIO_PROCESSING_COMPLETED, AUDIO_PROCESSING_FAILED
from apps.academics.audio import validate_audio_file
from apps.academics.audio import transcode_to_hls


def _make_lecture():
    """Return a simple mock with the attributes transcode_to_hls mutates."""
    lecture = MagicMock()
    lecture.id = 999
    lecture.audio_processing_status = ""
    lecture.audio_processing_error = ""
    lecture.audio_hls_playlist_key = ""
    lecture.audio_hls_segment_prefix = ""
    return lecture


class ValidateAudioFileTests(TestCase):
    def test_valid_extensions(self):
        for ext in [".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac"]:
            validate_audio_file(f"audio{ext}", "audio/mpeg")

    def test_rejects_invalid_extension(self):
        with self.assertRaises(ValueError):
            validate_audio_file("video.mp4", "video/mp4")

    def test_rejects_invalid_mime(self):
        with self.assertRaises(ValueError):
            validate_audio_file("audio.mp3", "application/pdf")


class TranscodeToHlsFfmpegArgsTests(TestCase):
    @patch("apps.academics.audio.subprocess.run")
    @patch("apps.academics.audio.storage_service.backend.upload")
    def test_ffmpeg_called_with_hls_args(self, mock_upload, mock_run):
        mock_run.return_value.returncode = 0
        lecture = _make_lecture()
        transcode_to_hls(lecture, b"fake", "test.mp3", "audio/mpeg")
        args, _ = mock_run.call_args
        cmd = args[0]
        self.assertIn("ffmpeg", cmd)
        self.assertIn("-hls_time", cmd)
        self.assertIn("10", cmd)


class TranscodeToHlsMetadataTests(TestCase):
    @patch("apps.academics.audio.subprocess.run")
    @patch("apps.academics.audio.storage_service.backend.upload")
    def test_saves_hls_metadata_on_success(self, mock_upload, mock_run):
        mock_run.return_value.returncode = 0
        lecture = _make_lecture()
        transcode_to_hls(lecture, b"data", "audio.mp3", "audio/mpeg")
        self.assertEqual(lecture.audio_processing_status, AUDIO_PROCESSING_COMPLETED)
        self.assertTrue(lecture.audio_hls_playlist_key.endswith("index.m3u8"))
        self.assertIn("seg_", lecture.audio_hls_segment_prefix)


class TranscodeToHlsFailureTests(TestCase):
    @patch("apps.academics.audio.subprocess.run")
    def test_ffmpeg_failure_sets_failed_status(self, mock_run):
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "ffmpeg error"
        lecture = _make_lecture()
        with self.assertRaises(RuntimeError):
            transcode_to_hls(lecture, b"data", "audio.mp3", "audio/mpeg")
        self.assertEqual(lecture.audio_processing_status, AUDIO_PROCESSING_FAILED)
        self.assertTrue(lecture.audio_processing_error)
