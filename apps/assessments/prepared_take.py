"""Pre-rendered HTMX Take chunks: render once, gzip once, serve bytes."""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
from datetime import datetime, timedelta

import redis
from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone

CHUNK_SIZE = 25
COMPRESSLEVEL = 5
VERSION = "v4"
TAKE_CACHE_BUDGET_BYTES = 80 * 1024 * 1024  # 80 MB rebuildable budget
TAKE_CACHE_TTL = 7 * 24 * 60 * 60  # 7 days secondary cleanup

# Key namespace per spec
# assessment-attempt:{attempt_id}:take:v4:chunk:{n}:gzip
# assessment-attempt:{attempt_id}:take:v4:meta
# assessment-attempt:{attempt_id}:take:v4:valid_choices


def _client():
    url = getattr(settings, "CACHE_URL", "")
    if not url:
        return None
    return redis.Redis.from_url(url, decode_responses=False)  # type: ignore[no-untyped-call]


def _text_client():
    url = getattr(settings, "CACHE_URL", "")
    if not url:
        return None
    return redis.Redis.from_url(url, decode_responses=True)  # type: ignore[no-untyped-call]


def _chunk_key(attempt_id: int, chunk_no: int) -> str:
    return f"assessment-attempt:{attempt_id}:take:{VERSION}:chunk:{chunk_no}:gzip"


def _meta_key(attempt_id: int) -> str:
    return f"assessment-attempt:{attempt_id}:take:{VERSION}:meta"


def _valid_choices_key(attempt_id: int) -> str:
    return f"assessment-attempt:{attempt_id}:take:{VERSION}:valid_choices"


def _budget_key() -> str:
    return f"assessment-attempt:take:{VERSION}:cache_bytes"


def _effective_close(attempt) -> datetime:
    """Return the earliest authoritative time at which this Take expires."""
    assessment = attempt.assessment
    deadlines = []
    if assessment.close_at:
        deadlines.append(assessment.close_at)
    if attempt.started_at and assessment.duration_minutes:
        deadlines.append(
            attempt.started_at + timedelta(minutes=assessment.duration_minutes),
        )
    if not deadlines:
        return timezone.now() + timedelta(seconds=TAKE_CACHE_TTL)
    return min(deadlines)


def _snapshot_hash(snapshot_questions) -> str:
    h = hashlib.sha256()
    for q in sorted(snapshot_questions, key=lambda x: x.order):
        h.update(str(q.order).encode())
        h.update(q.text_snapshot.encode())
        h.update(q.question_type.encode())
        for c in sorted(q.choices.all(), key=lambda x: x.order):
            h.update(str(c.order).encode())
            h.update(c.text_snapshot.encode())
    return h.hexdigest()[:16]


def _render_chunk_html(chunk_questions, attempt) -> str:
    # Reuse a fragment that renders one chunk without outer layout
    return render_to_string(
        "ui/fragments/take_chunk.html",
        {"questions": chunk_questions, "attempt": attempt},
    )


def chunk_snapshot_questions(questions, chunk_size: int = CHUNK_SIZE):
    """Pack whole lecture groups into chunks so a lecture is never split.

    Questions are ordered section by section in the snapshot. Keeping each
    lecture whole lets the take fragment render one collapsible group per
    lecture without repeating a group across lazy-loaded chunks. A lecture
    that is larger than ``chunk_size`` forms its own oversized chunk.
    """
    groups = []
    for question in questions:
        if groups and groups[-1][0].section_id == question.section_id:
            groups[-1].append(question)
        else:
            groups.append([question])

    chunks = []
    current = []
    for group in groups:
        if current and len(current) + len(group) > chunk_size:
            chunks.append(current)
            current = []
        current.extend(group)
    if current:
        chunks.append(current)
    return chunks


def build_prepared_take(attempt) -> dict:
    """Render all chunks for attempt and store gzip bytes. Returns meta."""
    client = _client()
    tclient = _text_client()
    if client is None:
        raise RuntimeError("Redis cache not configured")

    # Load immutable snapshot with choices
    qs = list(
        attempt.snapshot_questions.select_related("section__lecture")
        .order_by("order")
        .prefetch_related("choices")
    )
    if not qs:
        raise ValueError("No snapshot questions to prepare")

    snap_hash = _snapshot_hash(qs)
    # Split into chunks, keeping each lecture group whole
    chunks = chunk_snapshot_questions(qs)
    chunk_count = len(chunks)

    # Prepare compressed payloads
    prepared = []
    total_compressed = 0
    total_raw = 0
    for idx, chunk_qs in enumerate(chunks, start=1):
        html = _render_chunk_html(chunk_qs, attempt)
        raw = html.encode("utf-8")
        compressed = gzip.compress(raw, compresslevel=COMPRESSLEVEL)
        total_raw += len(raw)
        total_compressed += len(compressed)
        prepared.append((idx, compressed, len(raw), len(compressed)))

    # Budget guard - application level before write
    # Use tclient to estimate current budget if tracked, else scan (estimate)
    # We track a counter key _budget_key, fallback to INFO memory
    try:
        current_budget = int(tclient.get(_budget_key()) or 0) if tclient else 0
    except Exception:
        current_budget = 0
    if current_budget + total_compressed > TAKE_CACHE_BUDGET_BYTES:
        # Do not write if would exceed budget - log and fallback path will rebuild per request
        logging.getLogger(__name__).warning(
            "take_cache_budget_exceeded attempt=%s need=%s current=%s budget=%s",
            attempt.pk, total_compressed, current_budget, TAKE_CACHE_BUDGET_BYTES,
        )
        # Still return meta with not_cached flag so caller knows to fallback
        return {
            "version": VERSION,
            "question_count": len(qs),
            "chunk_count": chunk_count,
            "snapshot_hash": snap_hash,
            "prepared_at": timezone.now().isoformat(),
            "uncompressed_bytes": total_raw,
            "compressed_bytes": total_compressed,
            "cached": False,
            "reason": "budget_exceeded",
        }

    # Store each chunk as raw bytes with TTL
    expires_at = _effective_close(attempt)
    expires_epoch = max(int(expires_at.timestamp()), int(timezone.now().timestamp()) + 1)
    pipe = client.pipeline()
    for idx, compressed, _raw_len, _comp_len in prepared:
        pipe.set(_chunk_key(attempt.pk, idx), compressed)
        pipe.expireat(_chunk_key(attempt.pk, idx), expires_epoch)
    # Store valid choice map for submission fast path
    valid_map = {}
    for q in qs:
        if q.question_type == "mcq":
            valid_map[str(q.order)] = [c.pk for c in q.choices.all()]
        else:
            valid_map[str(q.order)] = []
    # Also store by question pk for robustness
    valid_map_pk = {str(q.pk): [c.pk for c in q.choices.all()] for q in qs}
    pipe.set(_valid_choices_key(attempt.pk), json.dumps({"by_order": valid_map, "by_pk": valid_map_pk}))
    pipe.expireat(_valid_choices_key(attempt.pk), expires_epoch)
    meta = {
        "version": VERSION,
        "question_count": len(qs),
        "chunk_count": chunk_count,
        "snapshot_hash": snap_hash,
        "prepared_at": timezone.now().isoformat(),
        "uncompressed_bytes": total_raw,
        "compressed_bytes": total_compressed,
        "chunk_size": CHUNK_SIZE,
        "compresslevel": COMPRESSLEVEL,
        "effective_close_at": expires_at.isoformat(),
    }
    pipe.set(_meta_key(attempt.pk), json.dumps(meta))
    pipe.expireat(_meta_key(attempt.pk), expires_epoch)
    # Update budget counter atomically
    pipe.incrby(_budget_key(), total_compressed)
    pipe.expire(_budget_key(), TAKE_CACHE_TTL)
    pipe.execute()

    return {**meta, "cached": True}


def get_chunk(attempt_id: int, chunk_no: int) -> bytes | None:
    client = _client()
    if client is None:
        return None
    return client.get(_chunk_key(attempt_id, chunk_no))


def _load_json(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def get_meta(attempt_id: int) -> dict | None:
    tclient = _text_client()
    if tclient is None:
        return None
    raw = tclient.get(_meta_key(attempt_id))
    data = _load_json(raw)
    return data if isinstance(data, dict) else None


def get_valid_choices(attempt_id: int) -> dict | None:
    tclient = _text_client()
    if tclient is None:
        return None
    raw = tclient.get(_valid_choices_key(attempt_id))
    data = _load_json(raw)
    return data if isinstance(data, dict) else None


def delete_prepared_take(attempt_id: int) -> int:
    """Delete all chunks/meta for attempt and adjust budget. Returns freed bytes."""
    client = _client()
    tclient = _text_client()
    if client is None:
        return 0
    meta = get_meta(attempt_id)
    freed = meta.get("compressed_bytes", 0) if meta else 0
    # Delete keys
    keys = [_chunk_key(attempt_id, i) for i in range(1, (meta.get("chunk_count", 4) if meta else 10) + 1)]
    keys.append(_meta_key(attempt_id))
    keys.append(_valid_choices_key(attempt_id))
    client.delete(*keys)
    if freed and tclient:
        try:
            tclient.decrby(_budget_key(), freed)
        except Exception:
            pass
    return freed

