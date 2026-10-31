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
from django.db import transaction
from django.utils import timezone

from apps.academics import services as academic_services
from apps.academics.audio import transcode_to_hls
from apps.academics.constants import (
    AUDIO_PROCESSING_COMPLETED,
    AUDIO_PROCESSING_FAILED,
    AUDIO_PROCESSING_PROCESSING,
    YOUTUBE_METADATA_STATUS_PENDING,
    YOUTUBE_METADATA_STATUS_READY,
    YOUTUBE_METADATA_STATUS_UNAVAILABLE,
)
from apps.academics.models import DiplomaLecture, Lecture
from apps.storage.services import record_deferred_storage_deletion, storage_service

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


def _youtube_request_queryset(model, lecture_id, video_id, request_id):
    queryset = model.objects.filter(pk=lecture_id, youtube_video_id=video_id)
    if request_id is None:
        return queryset.filter(youtube_metadata_request_id__isnull=True)
    return queryset.filter(youtube_metadata_request_id=request_id)


def _mark_metadata_unavailable(
    model,
    lecture_id,
    video_id,
    request_id,
    *,
    reason: str = "",
) -> None:
    """Persist failure only for the still-current video metadata request."""
    now = timezone.now()
    updated = _youtube_request_queryset(
        model, lecture_id, video_id, request_id,
    ).filter(youtube_metadata_status=YOUTUBE_METADATA_STATUS_PENDING).update(
        youtube_metadata_status=YOUTUBE_METADATA_STATUS_UNAVAILABLE,
        youtube_metadata_fetched_at=now,
        updated_at=now,
    )
    if not updated:
        logger.info("Ignoring stale YouTube metadata failure for lecture %s", lecture_id)
        return
    if reason:
        logger.error("YouTube metadata unavailable for lecture %s: %s", lecture_id, reason)
    else:
        logger.error("YouTube metadata unavailable for lecture %s", lecture_id)


@shared_task(ignore_result=True, max_retries=2, default_retry_delay=60)
def refresh_youtube_metadata_task(
    lecture_id: int,
    *,
    diploma: bool = False,
    expected_video_id: str | None = None,
    request_id: str | None = None,
) -> None:
    """Fetch duration/embeddable state from the official YouTube Data API.

    Only runs when ``settings.YOUTUBE_API_KEY`` is set. On success the
    duration is persisted into ``youtube_duration_seconds``. Completion or
    failure only applies while both the source video and request generation
    still match, so edits and retries cannot be overwritten by stale work.
    """
    model = DiplomaLecture if diploma else Lecture
    try:
        lecture = model.objects.get(id=lecture_id)
    except model.DoesNotExist:
        logger.warning("YouTube metadata task: lecture %s (diploma=%s) no longer exists", lecture_id, diploma)
        return

    video_id = expected_video_id or lecture.youtube_video_id
    if expected_video_id and expected_video_id != lecture.youtube_video_id:
        logger.info("Ignoring stale YouTube metadata task for lecture %s", lecture_id)
        return

    if request_id is None:
        # A queued pre-deployment task is valid only while no newer request has
        # claimed this lecture. New tasks always carry their request UUID.
        if lecture.youtube_metadata_request_id is not None:
            logger.info("Ignoring superseded YouTube metadata task for lecture %s", lecture_id)
            return
        request_token = None
    else:
        request_token = request_id
        if str(lecture.youtube_metadata_request_id or "") != request_id:
            logger.info("Ignoring stale YouTube metadata task for lecture %s", lecture_id)
            return

    if not video_id:
        logger.error("YouTube metadata unavailable for lecture %s: no video ID", lecture_id)
        return
    if (
        lecture.youtube_metadata_status == YOUTUBE_METADATA_STATUS_READY
        and lecture.youtube_duration_seconds
        and lecture.youtube_duration_seconds > 0
    ):
        return

    request_queryset = _youtube_request_queryset(model, lecture_id, video_id, request_token)
    if lecture.youtube_metadata_status != YOUTUBE_METADATA_STATUS_PENDING:
        now = timezone.now()
        claimed = request_queryset.filter(
            youtube_metadata_status__in=("", YOUTUBE_METADATA_STATUS_UNAVAILABLE),
        ).update(
            youtube_metadata_status=YOUTUBE_METADATA_STATUS_PENDING,
            youtube_metadata_fetched_at=None,
            updated_at=now,
        )
        if not claimed:
            logger.info("Ignoring inactive YouTube metadata task for lecture %s", lecture_id)
            return
    elif not request_queryset.filter(
        youtube_metadata_status=YOUTUBE_METADATA_STATUS_PENDING,
    ).exists():
        logger.info("Ignoring stale YouTube metadata task for lecture %s", lecture_id)
        return

    api_key = getattr(settings, "YOUTUBE_API_KEY", None)
    if not api_key:
        _mark_metadata_unavailable(
            model, lecture_id, video_id, request_token,
            reason="YOUTUBE_API_KEY not configured",
        )
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
            _mark_metadata_unavailable(model, lecture_id, video_id, request_token, reason="video not found")
            return
        item = items[0]
        embeddable = item.get("status", {}).get("embeddable", True)
        if not embeddable:
            _mark_metadata_unavailable(model, lecture_id, video_id, request_token, reason="video not embeddable")
            return
        duration = _iso8601_duration_to_seconds(item.get("contentDetails", {}).get("duration", ""))
        if duration is None or duration <= 0:
            _mark_metadata_unavailable(model, lecture_id, video_id, request_token, reason="invalid duration")
            return
        now = timezone.now()
        updated = request_queryset.filter(
            youtube_metadata_status=YOUTUBE_METADATA_STATUS_PENDING,
        ).update(
            youtube_duration_seconds=duration,
            youtube_metadata_status=YOUTUBE_METADATA_STATUS_READY,
            youtube_metadata_fetched_at=now,
            updated_at=now,
        )
        if not updated:
            logger.info("Ignoring stale YouTube metadata success for lecture %s", lecture_id)
    except Exception as exc:  # noqa: BLE001 - bounded external call, failure is persisted
        logger.error(
            "YouTube metadata fetch failed for lecture %s (%s)",
            lecture_id,
            type(exc).__name__,
        )
        _mark_metadata_unavailable(model, lecture_id, video_id, request_token, reason="fetch failed")


@shared_task(ignore_result=True)
def transcode_lecture_audio_task(
    lecture_id: int,
    storage_key: str,
    original_filename: str,
    content_type: str,
    storage_prefix: str,
    byte_size: int | None = None,
    checksum: str = "",
    *,
    diploma: bool = False,
) -> None:
    """Transcode an uploaded audio source to HLS and upload all output.

    Idempotent: skips when the lecture is already marked completed with the
    same source key. Every storage write goes through the storage service;
    the lecture is marked FAILED with a visible error on any exception.
    """
    def cleanup_generation(prefix, source_key=""):
        """Remove only one immutable generation and durably record failures."""
        try:
            if prefix and not storage_service.delete_prefix(prefix):
                record_deferred_storage_deletion(
                    prefix=prefix, error="Audio generation cleanup returned false",
                )
        except Exception as cleanup_exc:
            record_deferred_storage_deletion(prefix=prefix, error=str(cleanup_exc))
        if source_key and (not prefix or not source_key.startswith(f"{prefix.rstrip('/')}/")):
            try:
                if not storage_service.delete_file(source_key):
                    record_deferred_storage_deletion(
                        key=source_key, error="Audio source cleanup returned false",
                    )
            except Exception as cleanup_exc:
                record_deferred_storage_deletion(key=source_key, error=str(cleanup_exc))

    model = DiplomaLecture if diploma else Lecture
    try:
        with transaction.atomic():
            locked = model.objects.select_for_update().get(pk=lecture_id)
            active_generation_matches = (
                locked.audio_storage_key == storage_key
                and locked.audio_processing_status == AUDIO_PROCESSING_COMPLETED
                and locked.audio_hls_segment_prefix == storage_prefix.rstrip("/")
                and locked.audio_hls_playlist_key
            )
            if active_generation_matches:
                logger.info("Audio transcode for lecture %s already completed; skipping", lecture_id)
                return
            if locked.audio_pending_storage_key != storage_key:
                if locked.audio_hls_segment_prefix != storage_prefix.rstrip("/"):
                    transaction.on_commit(
                        lambda prefix=storage_prefix: cleanup_generation(prefix)
                    )
                logger.info("Ignoring stale audio task for lecture %s", lecture_id)
                return
            locked.audio_processing_status = AUDIO_PROCESSING_PROCESSING
            locked.audio_processing_error = ""
            locked.save(update_fields=["audio_processing_status", "audio_processing_error", "updated_at"])
    except model.DoesNotExist:
        logger.warning("Audio transcode task: lecture %s (diploma=%s) no longer exists", lecture_id, diploma)
        cleanup_generation(storage_prefix)
        return

    try:
        result = transcode_to_hls(
            storage_key,
            original_filename,
            storage_prefix=storage_prefix,
        )
        with transaction.atomic():
            locked = model.objects.select_for_update().get(pk=lecture_id)
            if locked.audio_pending_storage_key != storage_key:
                if locked.audio_hls_segment_prefix != storage_prefix.rstrip("/"):
                    transaction.on_commit(
                        lambda prefix=storage_prefix: cleanup_generation(prefix)
                    )
                logger.info("Discarded stale completed audio generation for lecture %s", lecture_id)
                return
            old_source_key = locked.audio_storage_key
            old_hls_prefix = locked.audio_hls_segment_prefix
            locked.audio_storage_key = storage_key
            locked.audio_pending_storage_key = ""
            locked.audio_original_filename = original_filename
            locked.audio_content_type = content_type
            locked.audio_byte_size = byte_size
            locked.audio_checksum = checksum
            locked.audio_hls_playlist_key = result["playlist_path"]
            locked.audio_hls_segment_prefix = result["storage_prefix"]
            locked.audio_duration_seconds = result["duration"]
            locked.audio_processing_status = AUDIO_PROCESSING_COMPLETED
            locked.audio_processing_error = ""
            locked.save(update_fields=[
                "audio_storage_key", "audio_pending_storage_key",
                "audio_original_filename", "audio_content_type",
                "audio_byte_size", "audio_checksum",
                "audio_hls_playlist_key", "audio_hls_segment_prefix",
                "audio_duration_seconds", "audio_processing_status",
                "audio_processing_error", "updated_at",
            ])
            if old_source_key or old_hls_prefix:
                transaction.on_commit(
                    lambda prefix=old_hls_prefix, key=old_source_key: cleanup_generation(
                        prefix, key,
                    )
                )
    except Exception as exc:
        logger.exception("Audio transcoding failed for lecture %s", lecture_id)
        with transaction.atomic():
            locked = model.objects.select_for_update().filter(pk=lecture_id).first()
            if locked is not None and locked.audio_pending_storage_key == storage_key:
                locked.audio_processing_status = AUDIO_PROCESSING_FAILED
                locked.audio_processing_error = str(exc)[:2000]
                locked.save(update_fields=["audio_processing_status", "audio_processing_error", "updated_at"])
            elif locked is None:
                transaction.on_commit(
                    lambda prefix=storage_prefix: cleanup_generation(prefix)
                )
        # Keep the immutable source generation for an idempotent retry. Any
        # partial HLS siblings are written at exact keys and will be replaced.
        raise


@shared_task(ignore_result=True)
def expire_stale_enrollments_task() -> dict:
    """Periodic lifecycle normalization for expired enrollments (P5-04).

    Bounded and idempotent: set-based conditional updates mark only active
    enrollments whose expiry has passed as expired. Returns operational counts
    without any student-sensitive data. Active access is never gated by this
    task — every read path independently enforces active/non-expired at read
    time, so a delayed run can never grant access.
    """
    counts = academic_services.expire_stale_enrollments()
    if any(counts.values()):
        logger.info("Expired enrollment normalization: %s", counts)
    else:
        logger.info("Expired enrollment normalization: nothing to expire")
    return counts
