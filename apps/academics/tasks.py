"""Celery tasks for academics background work.

These tasks are bounded and idempotent:
- ``refresh_youtube_metadata_task`` fetches official YouTube Data API metadata
  for one lecture (never from a student request path; S2-03).
- ``transcode_lecture_audio_task`` runs the FFmpeg HLS transcode for one
  lecture and uploads every output through the storage service (S2-09).

Both record an explicit persisted failure state on the lecture instead of
failing silently.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta

import requests
from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("apps.academics.tasks")

_YT_API_BASE = "https://www.googleapis.com/youtube/v3/videos"
_YT_API_TIMEOUT_SECONDS = 15

_ISO8601_DURATION_RE = re.compile(
    r"P(?:(?P<days>\d+)D)?T?(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?"
)


def _iso8601_duration_to_seconds(value: str) -> int | None:
    """Convert an ISO-8601 duration (e.g. PT1H2M3S) to total seconds."""
    match = _ISO8601_DURATION_RE.fullmatch(value or "")
    if not match:
        return None
    parts = {key: int(val) for key, val in match.groupdict().items() if val}
    return int(timedelta(**parts).total_seconds())


def _mark_metadata_unavailable(lecture, *, reason: str = "") -> None:
    """Persist the 'metadata unavailable' state on the lecture.

    The lecture model has no dedicated metadata-status field, so the state is
    recorded with the existing field set: ``youtube_metadata_fetched_at`` set
    (an attempt happened) with ``youtube_duration_seconds`` left NULL. Staff
    can see the attempt timestamp and the missing duration; the duration-
    weighted progress selector excludes lectures without a saved duration.
    """
    lecture.youtube_metadata_fetched_at = timezone.now()
    lecture.save(update_fields=["youtube_metadata_fetched_at"])
    if reason:
        logger.error("YouTube metadata unavailable for lecture %s: %s", lecture.id, reason)
    else:
        logger.error("YouTube metadata unavailable for lecture %s", lecture.id)


@shared_task(ignore_result=True, max_retries=2, default_retry_delay=60)
def refresh_youtube_metadata_task(lecture_id: int, *, diploma: bool = False) -> None:
    """Fetch duration/embeddable state from the official YouTube Data API.

    Only runs when ``settings.YOUTUBE_API_KEY`` is set. On success the
    duration is persisted into ``youtube_duration_seconds``; on any failure
    (missing key, API error, unembeddable video) the lecture is left with a
    persisted 'metadata unavailable' state and an ERROR log entry.
    """
    from apps.academics.models import DiplomaLecture, Lecture

    model = DiplomaLecture if diploma else Lecture
    try:
        lecture = model.objects.get(id=lecture_id)
    except model.DoesNotExist:
        logger.warning("YouTube metadata task: lecture %s (diploma=%s) no longer exists", lecture_id, diploma)
        return

    api_key = getattr(settings, "YOUTUBE_API_KEY", None)
    if not api_key:
        _mark_metadata_unavailable(lecture, reason="YOUTUBE_API_KEY not configured")
        return
    if not lecture.youtube_video_id:
        _mark_metadata_unavailable(lecture, reason="no video id")
        return

    try:
        response = requests.get(
            _YT_API_BASE,
            params={
                "part": "contentDetails,status",
                "id": lecture.youtube_video_id,
                "key": api_key,
            },
            timeout=_YT_API_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        items = response.json().get("items") or []
        if not items:
            _mark_metadata_unavailable(lecture, reason="video not found")
            return
        item = items[0]
        embeddable = item.get("status", {}).get("embeddable", True)
        if not embeddable:
            _mark_metadata_unavailable(lecture, reason="video not embeddable")
            return
        duration = _iso8601_duration_to_seconds(item.get("contentDetails", {}).get("duration", ""))
        if duration is None:
            _mark_metadata_unavailable(lecture, reason="unparseable duration")
            return
        lecture.youtube_duration_seconds = duration
        lecture.youtube_metadata_fetched_at = timezone.now()
        lecture.save(update_fields=["youtube_duration_seconds", "youtube_metadata_fetched_at"])
    except Exception as exc:  # noqa: BLE001 - bounded external call, failure is persisted
        logger.error("YouTube metadata fetch failed for lecture %s: %s", lecture_id, exc)
        _mark_metadata_unavailable(lecture, reason="fetch failed")


@shared_task(ignore_result=True)
def transcode_lecture_audio_task(
    lecture_id: int,
    storage_key: str,
    original_filename: str,
    content_type: str,
    storage_prefix: str,
    *,
    diploma: bool = False,
) -> None:
    """Transcode an uploaded audio source to HLS and upload all output.

    Idempotent: skips when the lecture is already marked completed with the
    same source key. Every storage write goes through the storage service;
    the lecture is marked FAILED with a visible error on any exception.
    """
    from apps.academics.audio import transcode_to_hls
    from apps.academics.constants import AUDIO_PROCESSING_COMPLETED
    from apps.academics.models import DiplomaLecture, Lecture

    model = DiplomaLecture if diploma else Lecture
    try:
        lecture = model.objects.get(id=lecture_id)
    except model.DoesNotExist:
        logger.warning("Audio transcode task: lecture %s (diploma=%s) no longer exists", lecture_id, diploma)
        return

    if (
        lecture.audio_processing_status == AUDIO_PROCESSING_COMPLETED
        and lecture.audio_storage_key == storage_key
        and lecture.audio_hls_playlist_key
    ):
        logger.info("Audio transcode for lecture %s already completed; skipping", lecture_id)
        return

    from apps.storage.services import storage_service

    source_data = storage_service.download_file(storage_key)
    transcode_to_hls(
        lecture,
        source_data,
        original_filename,
        content_type,
        storage_prefix=storage_prefix,
    )
