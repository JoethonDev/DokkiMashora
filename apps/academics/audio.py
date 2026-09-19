"""Audio processing service — FFmpeg HLS transcoding."""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.utils.translation import gettext_lazy as _

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


def _probe_duration(input_path: Path) -> int | None:
    """Measure audio duration with ffprobe. Returns whole seconds or None."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(input_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.warning("ffprobe failed: %s", result.stderr.strip()[:200])
            return None
        raw = result.stdout.strip()
        if not raw:
            return None
        return max(1, int(float(raw)))
    except (ValueError, subprocess.TimeoutExpired) as exc:
        logger.warning("Audio duration probe failed for %s: %s", input_path, exc)
        return None


def transcode_to_hls(
    source_storage_key: str,
    original_filename: str,
    *,
    storage_prefix: str,
) -> dict[str, int | str]:
    """Transcode one source and upload an addressable immutable HLS generation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = Path(tmpdir) / f"input{Path(original_filename).suffix}"
        output_dir = Path(tmpdir) / "hls"
        output_dir.mkdir(parents=True, exist_ok=True)

        storage_service.download_file_to_path(
            source_storage_key,
            input_path,
            max_size=settings.AUDIO_MAX_UPLOAD_MB * 1024 * 1024,
        )

        duration = _probe_duration(input_path)
        if duration is None:
            raise RuntimeError(_("Could not determine audio duration."))

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
            raise RuntimeError(_("FFmpeg failed: %(error)s") % {"error": result.stderr})

        for f in output_dir.iterdir():
            if not f.is_file():
                continue
            data = f.read_bytes()
            ctype = (
                "application/vnd.apple.mpegurl"
                if f.suffix == ".m3u8"
                else "video/MP2T"
            )
            storage_service.upload_generated_file(
                data,
                key=f"{storage_prefix.rstrip('/')}/{f.name}",
                content_type=ctype,
                max_size=settings.AUDIO_MAX_UPLOAD_MB * 1024 * 1024,
                allowed_types=[".m3u8", ".ts"],
            )
    generation = storage_prefix.rstrip("/").rsplit("/", 1)[-1]
    return {
        "duration": duration,
        "playlist_path": f"{generation}/index.m3u8",
        "storage_prefix": storage_prefix.rstrip("/"),
    }
