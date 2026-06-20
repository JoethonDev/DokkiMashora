"""Audio processing service — FFmpeg HLS transcoding."""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from apps.academics.constants import (
    AUDIO_PROCESSING_COMPLETED,
    AUDIO_PROCESSING_FAILED,
    AUDIO_PROCESSING_PROCESSING,
)
from apps.storage.services import storage_service

logger = logging.getLogger("apps.academics.audio")

_ALLOWED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac"}
_ALLOWED_AUDIO_MIME_TYPES = {
    "audio/mpeg",
    "audio/wav",
    "audio/ogg",
    "audio/mp4",
    "audio/aac",
    "audio/flac",
    "audio/x-m4a",
}


def validate_audio_file(filename: str, content_type: str) -> None:
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_AUDIO_EXTENSIONS:
        raise ValueError(
            _("Unsupported audio format '%(ext)s'. Allowed: %(allowed)s")
            % {"ext": ext, "allowed": ", ".join(sorted(_ALLOWED_AUDIO_EXTENSIONS))}
        )
    if content_type not in _ALLOWED_AUDIO_MIME_TYPES:
        raise ValueError(
            _("Unexpected MIME type '%(type)s'.")
            % {"type": content_type}
        )


def transcode_to_hls(
    lecture,
    file_data: bytes,
    original_filename: str,
    content_type: str,
) -> None:
    """Transcode uploaded audio to HLS, upload segments to storage."""
    lect_id = lecture.id
    prefix = f"lectures/{lect_id}/audio"
    playlist_key = f"{prefix}/index.m3u8"
    segment_prefix = f"{prefix}/seg_"

    lecture.audio_processing_status = AUDIO_PROCESSING_PROCESSING
    lecture.audio_processing_error = ""
    lecture.audio_hls_playlist_key = playlist_key
    lecture.audio_hls_segment_prefix = segment_prefix
    lecture.save(update_fields=[
        "audio_processing_status", "audio_processing_error",
        "audio_hls_playlist_key", "audio_hls_segment_prefix",
    ])

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / f"input{Path(original_filename).suffix}"
            output_dir = Path(tmpdir) / "hls"
            output_dir.mkdir(parents=True, exist_ok=True)

            input_path.write_bytes(file_data)

            playlist_path = output_dir / "index.m3u8"
            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", str(input_path),
                    "-c:a", "aac",
                    "-b:a", "64k",
                    "-vn",
                    "-hls_time", "10",
                    "-hls_list_size", "0",
                    "-hls_segment_filename",
                    str(output_dir / "seg_%03d.ts"),
                    str(playlist_path),
                ],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg failed: {result.stderr}")

            for f in output_dir.iterdir():
                if not f.is_file():
                    continue
                rel_key = f"{prefix}/{f.name}"
                data = f.read_bytes()
                if settings.USE_R2_STORAGE:
                    storage_service.upload_file(
                        data,
                        key_prefix=prefix,
                        content_type=(
                            "application/vnd.apple.mpegurl"
                            if f.suffix == ".m3u8"
                            else "video/MP2T"
                        ),
                        original_filename=f.name,
                        max_size=200 * 1024 * 1024,
                        allowed_types=[".m3u8", ".ts"],
                    )
                else:
                    storage_service.backend.upload(rel_key, data, "application/octet-stream")

        lecture.audio_processing_status = AUDIO_PROCESSING_COMPLETED
        lecture.save(update_fields=["audio_processing_status"])

    except Exception as exc:
        logger.exception("Audio transcoding failed for lecture %s", lect_id)
        lecture.audio_processing_status = AUDIO_PROCESSING_FAILED
        lecture.audio_processing_error = str(exc)
        lecture.save(update_fields=["audio_processing_status", "audio_processing_error"])
        raise
